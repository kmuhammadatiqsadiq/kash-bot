# Kash's Server bot

Posts a few important headlines (headline + link only) into your news, football and cricket
channels, and sends match-day reminders. No paid services, no credit card.

## What runs where
- GitHub Actions runs `bot.py` every ~15 minutes (free for standard runners in public repos).
- Discord webhooks deliver the messages. Feeds and football-data.org's free tier supply the data.
- `state.json` remembers what was already posted (the bot commits it back once a day or when it posts).

## Setup (about 15 minutes)
1. Make a free GitHub account and create a **public** repository (e.g. `kash-bot`).
   Keep it public: private repos only get a limited monthly allowance of free minutes.
2. Add these files. Easiest in the browser: **Add file -> Create new file**, type the name, paste the contents.
   For the workflow, type the name `.github/workflows/bot.yml` (typing the slashes makes the folders).
   `bot.py`, `config.json`, `fixtures.json`, `state.json`, `.github/workflows/bot.yml`
3. In Discord, for each of #news, #football, #cricket, #match-day: Edit Channel -> Integrations -> Webhooks -> New Webhook -> Copy Webhook URL.
4. In GitHub: Settings -> Secrets and variables -> Actions -> New repository secret. Add:
   - `WEBHOOK_NEWS`, `WEBHOOK_FOOTBALL`, `WEBHOOK_CRICKET`, `WEBHOOK_MATCHDAY` (the URLs from step 3)
   - `MATCHDAY_ROLE_ID` (optional): Discord -> Settings -> Advanced -> Developer Mode on, then right-click your Match Day role -> Copy Role ID
   - `FOOTBALL_DATA_KEY` (optional): free key from football-data.org/client/register, needed for automatic Real Madrid fixtures
   NEVER paste a webhook URL into a file. The repo is public; secrets are not.
5. Actions tab -> "Kash bot" -> Run workflow:
   1. mode **test**: each channel should get a "connected" message.
   2. mode **dry-run**: the log shows what WOULD be posted. This also proves the feed URLs work.
   3. Done. The schedule runs on its own after that.

## Tuning (edit config.json)
- `daily_cap`: max posts per channel per day (Dhaka time). `priority_extra`: extra posts allowed for priority keywords.
- `include` / `exclude` / `priority`: whole-word, case-insensitive keywords matched against the headline
  (include also checks the summary). Items matching no include keyword are dropped.
- `max_age_hours`: ignore stories older than this.
- Add or swap feeds in each channel's `feeds` list.

## Match-day fixtures
- Football: Real Madrid (team id 86) is fetched automatically if you set `FOOTBALL_DATA_KEY`. The free tier covers
  La Liga and the Champions League only. Check the id once at football-data.org if no reminders appear.
- Cricket and everything else: add entries to `fixtures.json` by hand (copy the example, use an +06:00 time).
  Delete the example entry when you add real ones.
- Reminders go out once, up to 60 minutes before kickoff, and ping the Match Day role if you set the role id.

## Keeping it alive (important)
GitHub switches off scheduled workflows in a public repo after 60 days with no repository activity. The bot makes a small
commit about once a day, which should count, but glance at the Actions tab monthly. If it says the workflow is disabled,
click Enable. Nothing here is guaranteed free forever: GitHub, football-data.org and the news sites can change their terms,
and a feed address can change. If a feed breaks, the log shows which one.
