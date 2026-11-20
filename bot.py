#!/usr/bin/env python3
"""Kash's Server bot: filtered news + match-day reminders -> Discord webhooks.

Standard library only. Posts headline + link only (never article text).
Modes:  python bot.py            real run (posts + saves state.json)
        python bot.py --dry-run  show what would be posted, change nothing
        python bot.py --test     send one "connected" message per webhook
"""
import argparse, hashlib, html, json, os, re, sys, time
import urllib.error, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
UA = "KashServerBot/1.0 (community Discord relay; headline and link only)"
DHAKA = timezone(timedelta(hours=6))  # Bangladesh has no daylight saving


# ----------------------------------------------------------------- helpers
def load_json(name, default):
    try:
        with open(os.path.join(ROOT, name), encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def save_json(name, data):
    with open(os.path.join(ROOT, name), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False, sort_keys=True)
        f.write("\n")


def http_get(url, headers=None, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def local(tag):
    return tag.rsplit("}", 1)[-1].lower()


def clean(text):
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    return re.sub(r"\s+", " ", text).strip()


def parse_date(s):
    s = (s or "").strip()
    if not s:
        return None
    for fn in (parsedate_to_datetime, lambda x: datetime.fromisoformat(x.replace("Z", "+00:00"))):
        try:
            d = fn(s)
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except Exception:
            pass
    return None


def kw_regex(words):
    """Whole-word, case-insensitive match for any keyword (so 'dies' won't hit 'studies')."""
    if not words:
        return None
    alt = "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))
    return re.compile(r"(?<!\w)(?:" + alt + r")(?!\w)", re.I)


def hex_to_int(h):
    return int(h.lstrip("#"), 16)


# ------------------------------------------------------------ feed parsing
def parse_feed(raw):
    root = ET.fromstring(raw)
    items = []
    for e in root.iter():
        if local(e.tag) not in ("item", "entry"):
            continue
        d = {"title": "", "link": "", "summary": "", "date": "", "id": ""}
        for c in e:
            n, t = local(c.tag), (c.text or "").strip()
            if n == "title":
                d["title"] = clean(t)
            elif n == "link":
                href = c.attrib.get("href")
                if href:
                    if not d["link"] or c.attrib.get("rel", "alternate") == "alternate":
                        d["link"] = href
                elif t and not d["link"]:
                    d["link"] = t
            elif n in ("description", "summary", "content", "encoded") and not d["summary"]:
                d["summary"] = clean(t)
            elif n in ("pubdate", "published", "updated", "date") and not d["date"]:
                d["date"] = t
            elif n in ("guid", "id") and not d["id"]:
                d["id"] = t
        d["published"] = parse_date(d["date"])
        items.append(d)
    return items


# ----------------------------------------------------------------- discord
def send_webhook(url, payload, dry_run):
    if dry_run:
        return True
    body = json.dumps(payload).encode()
    for attempt in range(2):
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": "application/json", "User-Agent": UA})
        try:
            urllib.request.urlopen(req, timeout=20).read()
            time.sleep(1.2)  # stay well under Discord's webhook rate limit
            return True
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt == 0:
                try:
                    wait = float(json.loads(e.read()).get("retry_after", 2))
                except Exception:
                    wait = 2.0
                time.sleep(min(wait, 15) + 0.5)
                continue
            print(f"  webhook error {e.code}")
            return False
        except Exception as e:
            print(f"  webhook error: {e}")
            return False
    return False


# -------------------------------------------------------------------- news
def process_channel(name, cfg, state, now, dry_run):
    url = os.environ.get(cfg["webhook_env"], "").strip()
    if not url and not dry_run:
        print(f"[{name}] skipped: secret {cfg['webhook_env']} is not set")
        return 0, 0
    today = now.astimezone(DHAKA).strftime("%Y-%m-%d")
    daily = state["daily"].setdefault(today, {})
    sent_today = daily.get(name, 0)
    cap, extra = cfg.get("daily_cap", 5), cfg.get("priority_extra", 2)
    inc, exc, pri = (kw_regex(cfg.get(k)) for k in ("include", "exclude", "priority"))
    max_age = timedelta(hours=cfg.get("max_age_hours", 24))
    seen = set(state["seen"])
    ok_feeds, candidates, done_keys, titles = 0, [], [], set()

    for feed in cfg["feeds"]:
        try:
            items = parse_feed(http_get(feed["url"]))
        except Exception as e:
            print(f"[{name}] feed failed: {feed['name']}: {e}")
            continue
        ok_feeds += 1
        for it in items:
            if not it["title"] or not it["link"]:
                continue
            key = hashlib.sha1((it["id"] or it["link"]).encode()).hexdigest()[:16]
            if key in seen:
                continue
            if it["published"] and now - it["published"] > max_age:
                continue
            title_n = it["title"].lower()
            if exc and exc.search(it["title"]):
                done_keys.append(key)
                continue
            score = 0
            if inc:
                score = 2 * len(inc.findall(it["title"])) + len(inc.findall(it["summary"]))
                if score == 0:
                    done_keys.append(key)
                    continue
            if title_n in titles:
                done_keys.append(key)
                continue
            titles.add(title_n)
            candidates.append({"key": key, "it": it, "src": feed["name"], "score": score,
                               "prio": bool(pri and pri.search(it["title"]))})
    if ok_feeds == 0:
        return 0, len(cfg["feeds"])

    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    candidates.sort(key=lambda c: (c["prio"], c["score"], c["it"]["published"] or epoch), reverse=True)
    posted = 0
    for c in candidates:
        limit = cap + extra if c["prio"] else cap
        if sent_today >= limit:
            done_keys.append(c["key"])  # over the daily cap: drop it, don't queue it
            continue
        it = c["it"]
        embed = {"title": it["title"][:250], "url": it["link"], "color": hex_to_int(cfg["color"]),
                 "footer": {"text": c["src"]}}
        if it["published"]:
            embed["timestamp"] = it["published"].astimezone(timezone.utc).isoformat()
        payload = {"username": cfg.get("username", "Kash News"), "embeds": [embed],
                   "allowed_mentions": {"parse": []}}
        tag = "PRIORITY " if c["prio"] else ""
        print(f"[{name}] {tag}{c['src']}: {it['title']}")
        if send_webhook(url, payload, dry_run):
            posted += 1
            sent_today += 1
            done_keys.append(c["key"])
    if not dry_run:
        daily[name] = sent_today
        state["seen"].extend(done_keys)
    return posted, 0


# --------------------------------------------------------------- match day
def gather_fixtures(cfg, now):
    out = []
    key = os.environ.get("FOOTBALL_DATA_KEY", "").strip()
    if key:
        d0 = (now - timedelta(days=1)).date().isoformat()
        d1 = (now + timedelta(days=3)).date().isoformat()
        for team in cfg.get("teams", []):
            try:
                u = f"https://api.football-data.org/v4/teams/{team['football_data_id']}/matches?dateFrom={d0}&dateTo={d1}"
                data = json.loads(http_get(u, {"X-Auth-Token": key}))
                for m in data.get("matches", []):
                    if m.get("status") not in ("SCHEDULED", "TIMED"):
                        continue
                    ko = parse_date(m.get("utcDate"))
                    if not ko:
                        continue
                    h = m["homeTeam"].get("shortName") or m["homeTeam"].get("name")
                    a = m["awayTeam"].get("shortName") or m["awayTeam"].get("name")
                    out.append({"id": f"fd-{m['id']}", "title": f"{h} vs {a}", "kickoff": ko,
                                "competition": m.get("competition", {}).get("name", ""), "emoji": "⚽"})
            except Exception as e:
                print(f"[matchday] football-data failed for {team.get('name')}: {e}")
    for f in load_json("fixtures.json", []):
        ko = parse_date(f.get("kickoff"))
        if ko and f.get("id") and f.get("title"):
            out.append({"id": f["id"], "title": f["title"], "kickoff": ko,
                        "competition": f.get("competition", ""), "emoji": f.get("emoji", "🏏")})
    return out


def process_matchday(cfg, state, now, dry_run):
    url = os.environ.get(cfg["webhook_env"], "").strip()
    if not url and not dry_run:
        print("[matchday] skipped: webhook secret not set")
        return 0
    role = os.environ.get("MATCHDAY_ROLE_ID", "").strip()
    before = cfg.get("remind_minutes_before", 60)
    reminded = set(state["reminded"])
    sent = 0
    for f in sorted(gather_fixtures(cfg, now), key=lambda x: x["kickoff"]):
        mins = (f["kickoff"] - now).total_seconds() / 60
        if f["id"] in reminded or not (-10 <= mins <= before):
            continue
        ts = int(f["kickoff"].timestamp())
        desc = f"Kickoff <t:{ts}:F> (<t:{ts}:R>)"
        if f["competition"]:
            desc += f"\n{f['competition']}"
        payload = {"username": cfg.get("username", "Match Day"),
                   "content": f"<@&{role}>" if role else "",
                   "embeds": [{"title": f"{f['emoji']} Match day: {f['title']}", "description": desc,
                               "color": hex_to_int(cfg["color"])}],
                   "allowed_mentions": {"parse": [], "roles": [role] if role else []}}
        print(f"[matchday] {f['title']} ({mins:.0f} min to kickoff)")
        if send_webhook(url, payload, dry_run):
            sent += 1
            if not dry_run:
                state["reminded"].append(f["id"])
    return sent


# -------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--test", action="store_true")
    args = ap.parse_args()
    cfg = load_json("config.json", None)
    if not cfg:
        sys.exit("config.json missing")
    now = datetime.now(timezone.utc)

    if args.test:
        for name, c in {**cfg["channels"], "matchday": cfg["matchday"]}.items():
            u = os.environ.get(c["webhook_env"], "").strip()
            if not u:
                print(f"[{name}] no secret {c['webhook_env']}")
                continue
            ok = send_webhook(u, {"username": c.get("username", "Kash Bot"),
                                  "content": f"✅ Kash bot is connected to this channel ({name})."}, False)
            print(f"[{name}] test message {'sent' if ok else 'FAILED'}")
        return

    state = load_json("state.json", {})
    for k, v in (("seen", []), ("daily", {}), ("reminded", []), ("last_run_date", "")):
        state.setdefault(k, v)

    total, failed_all = 0, 0
    for name, c in cfg["channels"].items():
        posted, dead = process_channel(name, c, state, now, args.dry_run)
        total += posted
        failed_all += 1 if dead else 0
    total += process_matchday(cfg["matchday"], state, now, args.dry_run)
    print(f"done: {total} message(s){' (dry run)' if args.dry_run else ''}")

    if not args.dry_run:
        today = now.astimezone(DHAKA).date()
        state["daily"] = {d: v for d, v in state["daily"].items()
                          if d >= (today - timedelta(days=3)).isoformat()}
        state["seen"] = state["seen"][-4000:]
        state["reminded"] = state["reminded"][-500:]
        state["last_run_date"] = today.isoformat()  # one small commit a day keeps the repo active
        save_json("state.json", state)
    if failed_all and failed_all == len(cfg["channels"]):
        sys.exit("every news feed failed")


if __name__ == "__main__":
    main()
