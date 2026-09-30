neo_build.py is a curses-based terminal UI for managing a Neocities site through the Neocities API.

It provides local and remote file browsers so common site-management tasks can be performed without manually typing or remembering full paths.

Features

Browse the remote Neocities directory tree

Browse local files and directories

Upload one or multiple files

Download remote files

Create remote directories

Rename or move remote files and directories

Delete remote files/directories

Compare local files against remote SHA-1 hashes

Upload only files that have changed

View Neocities site information

Filter directory listings from inside the TUI

Configure a local project root and download directory

Load the Neocities API key from a local .env file

Uploads preserve paths relative to the configured local project root.

For example, if the local root is:

~/website/

and you select:

~/website/images/logo.png

the tool can upload it as:

images/logo.png

under the remote directory selected in the TUI.

Requirements

Python 3

requests

A Neocities API key

On Debian, Kali, or Ubuntu:

sudo apt install python3-requests

API Key

Create a .env file in the same directory as neo_build.py:

NEOCITIES_API_KEY=your_api_key_here

Protect it:

chmod 600 .env

The key lookup order is:

NEOCITIES_API_KEY already exported in the environment

.env beside neo_build.py

~/.config/neocities-tui/config.json

The .env loader is built into the script, so python-dotenv is not required.

Do not commit .env to GitHub.

A basic .gitignore should include:

.env
__pycache__/
*.pyc

Running

python3 neo_build.py

Or:

chmod +x neo_build.py
./neo_build.py

You can also override the local project directory or download directory:

python3 neo_build.py --local-root ~/website

python3 neo_build.py --download-dir ~/Downloads/neocities

To prevent changed path settings from being written on exit:

python3 neo_build.py --no-save

Main Menu

1  Browse remote site
2  Upload local files
3  Download remote file
4  Create remote directory
5  Rename / move remote path
6  Delete remote files/directories
7  Compare local files with remote hashes
8  Site information
9  Settings

Both the local and remote browsers support normal directory navigation, filtering, multi-selection where appropriate, and directory selection from inside the TUI.

Typical controls include:

↑ / ↓       Navigate
Enter       Open / select
Backspace   Parent directory
Space       Mark/unmark files
d           Finish selection / choose directory
/           Filter
c           Clear filter
r           Refresh remote listing
q / Esc     Back

Uploading Files

The upload workflow uses the local file browser first, then the remote directory browser.

Typical flow:

Upload local files
        ↓
Browse local filesystem
        ↓
Select one or more files
        ↓
Choose destination remote directory
        ↓
Confirm upload

This avoids having to manually type absolute local or remote paths.

When a local project root is configured, paths beneath that root can be preserved during upload.

Example:

Local project root:
~/website

Selected file:

~/website/assets/images/logo.png

Remote path:

assets/images/logo.png

Moving and Renaming Remote Files

The Rename / move remote path option can move a file between directories and optionally rename it at the same time.

Example:

images/logo.png
        ↓
assets/site-logo.png

Choose the existing file, browse to the new parent directory, then enter the desired filename.

The same operation can also be used to move a file without renaming it:

images/logo.png
        ↓
assets/logo.png

The destination directory must already exist.

If it does not, create it first using:

Create remote directory

Creating Remote Directories

The directory creation workflow lets you browse to the desired parent directory and then enter only the new directory name.

Example:

/
└── assets/

To create:

/assets/images/

browse to:

/assets/

then enter:

images

Downloading Files

Remote files can be selected through the remote browser and downloaded into the configured local download directory.

The tool recreates the remote relative directory structure locally where appropriate.

Example remote file:

assets/images/logo.png

Configured download directory:

~/Downloads/neocities

Downloaded path:

~/Downloads/neocities/assets/images/logo.png

Downloaded files are first written to a temporary .part file and moved into place after the download completes.

Hash Comparison

The hash comparison mode calculates local SHA-1 hashes and compares them with the remote copies through the Neocities upload_hash API.

The workflow is designed to make incremental publishing easier.

Typical flow:

Compare local files with remote hashes
        ↓
Select local files
        ↓
Choose corresponding remote directory
        ↓
Calculate local SHA-1 hashes
        ↓
Query Neocities upload_hash
        ↓
Display changed / unchanged files
        ↓
Optionally upload only changed files

This helps avoid uploading files that already match the remote version.

Deleting Remote Files

Remote files and directories can be selected through the TUI.

Deletion requires explicitly typing:

DELETE

before the API request is sent.

This extra confirmation is intended to reduce accidental removal of remote site content.

Site Information

The site information view can display information about the authenticated Neocities site.

It can also query public site information for another Neocities site when a sitename is supplied.

Settings

The settings screen can configure:

Local project root

Download directory

API key fallback

Other persistent TUI preferences

Saved configuration is stored under:

~/.config/neocities-tui/config.json

The configuration file is intended to use private user-only permissions.

If the API key is loaded from the local .env file, the tool does not need to duplicate it into the JSON configuration.

Directory Browsing

One of the main goals of neo_build.py is to avoid requiring users to remember long local and remote paths.

Instead of manually entering paths such as:

/home/user/projects/site/assets/images/backgrounds/

you can navigate interactively through the curses browser.

The same idea applies to the remote Neocities site tree.

This makes operations such as uploading, moving, downloading, and deleting files closer to using a terminal file manager than a command-line API client.

Path Handling

Remote paths are treated as site-relative paths.

For example:

index.html
assets/style.css
images/banner.png
projects/demo/index.html

Remote paths containing .. are rejected by the client.

This prevents accidental traversal outside the intended remote path structure.

Security Notes

Keep your API key private.

Do not commit:

.env

to a public repository.

Recommended permissions:

chmod 600 .env

A suggested .gitignore:

.env
__pycache__/
*.pyc
*.log

The Neocities API key should be treated like a password because it allows modification of your site.

Neocities API

neo_build.py uses the official Neocities API:

https://neocities.org/api

The tool is intended as a terminal frontend for common API operations rather than a replacement for Neocities itself.

Notes

neo_build.py is a client for managing your own Neocities content.

Neocities remains responsible for the remote service, API behavior, storage limits, supporter features, account restrictions, and server-side validation.

The tool is primarily designed for people who prefer a keyboard-driven terminal workflow but still want visual file and directory selection instead of manually typing every path.
