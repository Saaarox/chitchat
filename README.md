# GuardBot

A production-grade, feature-rich Telegram group management bot.

## Features
- **Moderation**: Full suite of tools for banning, muting, kicking, and warning users.
- **Anti-Spam**: Filters links, forwards, and banned words.
- **Anti-Flood**: Prevents users from spamming the chat with too many messages.
- **Captcha**: Verifies new members with a math challenge to prevent raids.
- **Welcome/Goodbye**: Customizable messages for new and departing members.
- **Content Locks**: Granular control to lock/unlock media, stickers, polls, and more.
- **Analytics**: Tracks user activity and provides commands to view stats like `/top10`.
- **Night Mode**: Automatically locks the chat during specified hours.
- **Role System**: Custom, database-backed roles like Moderator, Cleaner, and Trusted.
- **Log Channel**: Reports all moderation actions to a designated channel.
- **CAS Integration**: Automatically bans users listed in the Combot Anti-Spam database.

## Commands

### Moderation
`/ban`, `/unban`, `/kick`, `/mute`, `/unmute`, `/tmute`, `/tban`, `/warn`, `/unwarn`, `/warns`, `/resetwarns`, `/del`, `/purge`, `/pin`, `/silentpin`, `/unpin`

### Info
`/id`, `/info`, `/admins`, `/staff`, `/rules`, `/setrules`, `/reload`

### Locks
`/lock`, `/unlock`, `/lockall`, `/locklist`

### Settings
`/settings`

### Welcome
`/setwelcome`, `/setgoodbye`

### Admin
`/setlogchannel`, `/addrole`, `/removerole`, `/listroles`

### Analytics
`/top10`, `/stat`, `/trend`, `/inactives`

## Setup (Local)
1. Clone the repository.
2. Copy `.env.example` to `.env` and fill in your `BOT_TOKEN`.
3. Run `docker-compose up --build`.

## Deploy to Railway
1. Create a new project on Railway.
2. Add PostgreSQL and Redis plugins from the marketplace.
3. In your project settings, go to the "Variables" tab and set the following:
   - `BOT_TOKEN`: Your token from BotFather.
   - `DATABASE_URL`: This is automatically provided by the Railway PostgreSQL plugin.
   - `REDIS_URL`: This is automatically provided by the Railway Redis plugin.
4. Deploy from your GitHub repository. Railway will automatically use the `Dockerfile`.

## Tech Stack
- Python 3.11, aiogram 3.x
- PostgreSQL + SQLAlchemy (async)
- Redis (redis.asyncio)
- Docker + Railway
