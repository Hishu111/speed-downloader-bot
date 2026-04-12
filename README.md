# Speed Downloader Telegram Bot

An ultra-fast media downloader Telegram bot powered by `yt-dlp` and `FFmpeg`, capable of downloading videos and music from over 1000+ websites, including YouTube, Instagram, SoundCloud, and more.

## Features

-   **High-Quality Downloads**: Supports various video qualities (8K, 4K, 1440p, 1080p, 720p, 480p) and audio formats (MP3, M4A, FLAC, OPUS, WAV) with adjustable bitrates.
-   **Extensive Platform Support**: Downloads from YouTube, Instagram, SoundCloud, Bandcamp, Mixcloud, Vimeo, Facebook, X (Twitter), TikTok, and many others.
-   **Music Search**: Search and download music directly within Telegram using the `/music` command.
-   **Playlist Downloads**: Download entire playlists with the `/playlist` command.
-   **Customizable Settings**: Users can adjust their preferred quality, audio format, bitrate, and thumbnail/subtitle embedding via the `/settings` command.
-   **Group Chat Control**: In group chats, music search is only triggered by the `/music` command to prevent accidental searches on plain text messages. In private chats, auto-search on plain text is enabled.

## Deployment Guide (Free Hosting)

This guide provides step-by-step instructions to deploy your Speed Downloader Telegram Bot on free hosting platforms like Render and Railway. Both platforms support Docker deployments, making it easy to get your bot up and running.

### Prerequisites

Before you begin, ensure you have the following:

1.  **Telegram Bot Token**: Obtain a bot token from BotFather on Telegram. Save this token securely.
2.  **GitHub Account**: You will need a GitHub account to store your bot's code.
3.  **Render/Railway Account**: Create an account on your preferred hosting platform.

### Step 1: Prepare Your Code

Ensure your `bot.py`, `requirements.txt`, `Procfile`, `Dockerfile`, and `.gitignore` are in the root directory of your project. These files are already prepared for you.

### Step 2: Create a GitHub Repository

1.  Go to [GitHub](https://github.com/) and create a new public repository (e.g., `speed-downloader-bot`).
2.  Initialize a local Git repository in your project folder and push your code to GitHub:

    ```bash
    git init
    git add .
    git commit -m "Initial commit: Speed Downloader Bot"
    git branch -M main
    git remote add origin https://github.com/YOUR_USERNAME/speed-downloader-bot.git
    git push -u origin main
    ```

    *Replace `YOUR_USERNAME` with your GitHub username.*

### Step 3: Deploy to Render

[Render](https://render.com/) is a unified platform to build and run all your apps and websites with automatic deployments from Git.

1.  **Create a New Web Service**: Log in to Render, go to your dashboard, and click "New" -> "Web Service."
2.  **Connect to GitHub**: Select your `speed-downloader-bot` repository.
3.  **Configure Your Service**:
    *   **Name**: `speed-downloader-bot` (or your preferred name)
    *   **Root Directory**: `/` (if your files are in the root)
    *   **Runtime**: `Docker`
    *   **Build Command**: (Leave empty, Dockerfile handles it)
    *   **Start Command**: (Leave empty, Dockerfile handles it)
    *   **Plan Type**: `Free` (or choose a paid plan if needed)
4.  **Add Environment Variable**: Under "Advanced" -> "Environment Variables," add a new variable:
    *   **Key**: `BOT_TOKEN`
    *   **Value**: Your Telegram bot token (e.g., `8707229530:AAHqUWiWy_ja7dUIrE7HmUxSRbB549w5lYM`)
5.  **Create Web Service**: Click "Create Web Service." Render will now build and deploy your bot.

### Step 4: Deploy to Railway

[Railway](https://railway.app/) is a modern app platform that makes it easy to deploy production-ready apps.

1.  **Create a New Project**: Log in to Railway, go to your dashboard, and click "New Project" -> "Deploy from GitHub Repo."
2.  **Connect to GitHub**: Select your `speed-downloader-bot` repository.
3.  **Configure Your Service**:
    *   Railway will automatically detect your `Dockerfile` and suggest a Docker deployment.
    *   **Service Name**: `speed-downloader-bot` (or your preferred name)
4.  **Add Variable**: Go to the "Variables" tab for your new service and add a new variable:
    *   **Name**: `BOT_TOKEN`
    *   **Value**: Your Telegram bot token (e.g., `8707229530:AAHqUWiWy_ja7dUIrE7HmUxSRbB549w5lYM`)
5.  **Deploy**: Railway will automatically build and deploy your bot. You can monitor the deployment logs in the dashboard.

## Usage

Once your bot is deployed and running, you can interact with it on Telegram:

-   Send a URL to download media.
-   Use `/music <query>` to search for and download music.
-   Use `/playlist <url>` to download an entire playlist.
-   Use `/settings` to customize your download preferences.

Enjoy your Speed Downloader Bot!
