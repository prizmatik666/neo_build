neo_build.py

<img width="431" height="487" alt="image" src="https://github.com/user-attachments/assets/8b09d100-6104-4bfb-bde5-5d93266d3ab8" />

A keyboard-driven curses TUI for managing a Neocities site from the terminal.

neo_build.py wraps the Neocities API in simple file browsers, so most jobs are point-and-select instead of typing remote paths by hand.

What you can do

Browse files and directories on your Neocities site

Upload one or several local files

Download a remote file

Create remote directories

Move or rename remote files and directories

Delete remote files and directories

Compare local files with remote SHA-1 hashes

Upload only files that have changed

View basic site information

Set a local project root and download directory

Requirements

Python 3

requests

A Neocities API key

A terminal with curses support

On Debian, Kali, or Ubuntu:

sudo apt install python3-requests

API key

The easiest setup is a .env file beside neo_build.py:

NEOCITIES_API_KEY=your_api_key_here

Then run:

python3 neo_build.py

The tool also accepts NEOCITIES_API_KEY from your shell environment. If no key is found, it will ask for one before opening the TUI.

Main menu

1  Browse remote site
2  Upload local files
3  Download remote file
4  Create remote directory
5  Rename / move remote path
6  Delete remote files/directories
7  Compare local files with remote hashes
8  Site information
9  Settings

Browser controls

The local and remote file browsers use the same basic controls:

Up / Down      Move through the list
Enter          Open a directory or select an item
Backspace      Go to the parent directory
/              Filter the current listing
c              Clear the filter
r              Refresh a remote listing
q / Esc        Go back

When selecting several files:

Space          Mark or unmark an item
d              Finish the selection

When choosing a destination directory:

d              Use the directory currently being viewed

Local root

The local root is simply the top of the local site tree that neo_build.py uses when building upload paths.

For example, suppose your local root is:

/home/user/my-site

and you select:

/home/user/my-site/images/logo.png

The part underneath the local root is:

images/logo.png

If you choose the remote site root as the destination, the file becomes:

/images/logo.png

If you choose a remote directory named backup, it becomes:

/backup/images/logo.png

This lets you select files through the browser while keeping their local folder structure.

You can change the local root from Settings or when starting the program:

python3 neo_build.py --local-root ~/my-site

Uploading files

Choose:

2  Upload local files

Then:

Browse your local project.

Mark files with Space.

Press d when finished.

Browse the remote site.

Enter the directory where the selected files should go.

Press d to choose that directory.

Review the upload plan and confirm.

The upload plan shows the local file and the remote path it will use before anything is sent.

Moving or renaming remote files

Choose:

5  Rename / move remote path

Select a remote file or directory, choose its new parent directory, then enter its new name.

To move a file without renaming it, keep the same filename.

Example:

/images/logo.png

can be moved to:

/assets/logo.png

Or moved and renamed in the same step:

/images/logo.png

to:

/assets/site-logo.png

Creating directories

Choose:

4  Create remote directory

Browse to the parent directory, press d, and enter the new directory name.

You only need to type the new name; the parent path comes from the browser.

Downloading files

Choose:

3  Download remote file

Select a file from the remote browser and it will be saved under your configured download directory.

By default:

~/Downloads/neocities

Remote folders are kept in the downloaded path. For example:

/assets/images/logo.png

is saved as:

~/Downloads/neocities/assets/images/logo.png

You can change the download directory in Settings or with:

python3 neo_build.py --download-dir ~/Downloads/my-neocities-files

Comparing local and remote files

Choose:

7  Compare local files with remote hashes

Select local files, choose the matching remote base directory, and neo_build.py will calculate SHA-1 hashes and ask Neocities which files already match.

The result shows:

Up to date: N
Needs upload: N

If changed files are found, the TUI can upload only those files immediately.

This is useful when you have edited a few files and do not want to resend everything.

Deleting files

Choose:

6  Delete remote files/directories

Mark the items you want to remove and finish the selection with d.

Before deletion, the TUI shows the selected paths and asks you to type:

DELETE

Deleting a remote directory also deletes the contents inside it.

Site information

The Site information screen can show information for your authenticated site or look up public information for another Neocities sitename.

Settings

The settings screen lets you change:

API key

Local root

Download directory

Settings are stored in:

~/.config/neocities-tui/config.json

A .env or shell-provided NEOCITIES_API_KEY takes precedence over a key stored in the config file.

Command-line options

--local-root PATH       Start with PATH as the local project root
--download-dir PATH     Change the download destination
--no-save               Do not save changed path settings when the program exits

Example:

python3 neo_build.py --local-root ~/my-site --download-dir ~/Downloads/site-backups

Neocities API

neo_build.py uses the official Neocities developer API:

https://neocities.org/api
