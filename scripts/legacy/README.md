# Legacy

`ImportScript` is the Google Apps Script the Kill Tracker started with: it read OCAP
`.json.gz` recordings from a Drive folder and wrote one row per player per mission into
a sheet.

It is **not used any more** — the bot counts recordings itself (`utils/killtracker.py`,
a port of this script that produces the same 35 columns). It is kept because it is the
reference for everything the tests do not cover: after a change to a counting rule, run it
under Node with the Apps Script services stubbed against a few real recordings and compare
the rows cell by cell. That is how the port was checked (0 differences on three recordings).
