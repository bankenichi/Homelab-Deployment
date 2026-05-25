# Proton MCP — common multi-tool workflows

Loaded on demand. These are recipes for tasks that span multiple tools.

## Find an email and reply

1. `mail__search_messages` with the keywords the user mentioned. Note the `id` AND `folder` on the most likely hit.
2. `mail__get_message` with that `id` + `folder` to confirm you have the right one.
3. Confirm with the user if more than one hit looked plausible.
4. `mail__reply_message` with the same `id` + `folder`. Set `reply_all` if the original had multiple recipients.

## Catch up on unread

1. `mail__get_unread` — gets the count and the 20 most recent unseen.
2. Summarize for the user. Don't dump the whole list.
3. If the user picks one, `mail__get_message` for the full body.

## Log into a site that has 2FA stored in Pass

1. `pass__search_items` with the site name to confirm the exact item title.
2. `pass__get_item` to grab `username` and `password`.
3. `pass__get_totp` to grab the current 2FA code.
4. Pass the three values directly into the next step (e.g. browser login). Don't echo the password or the TOTP code into the chat unless the user explicitly asked.

## Create a new credential with a fresh password

1. `pass__generate_password` (optional `length`).
2. Show the generated password to the user — they need to see it once.
3. `pass__create_item` with `title`, `username`, the generated `password`, and `url`.

## Update an existing credential's password (e.g. after a forced rotation)

1. `pass__search_items` to confirm the exact title.
2. `pass__generate_password` for a new one.
3. `pass__update_item` with `name=<title>`, `password=<new>`.
4. Confirm to the user the change went through. Suggest they also update the password in the actual site if they haven't.

## Back up a local folder to Proton Drive

1. `drive__list` to make sure the destination doesn't already exist.
2. `drive__mkdir` if you need to create the destination directory.
3. `drive__upload_folder` with the local path and the destination directory.
4. `drive__list` the destination to confirm files arrived.

## Move an inbox message to Archive and re-find it

Sequence numbers change after a move. If you need to act on the same message again:

1. Note the `message_id_header` (the real RFC822 `<...>` Message-ID) BEFORE moving.
2. `mail__move_message` with `message_id`, `destination="Archive"`, `folder="INBOX"`.
3. `mail__list_folder_messages` with `folder="Archive"` and look for the matching `message_id_header`, OR `mail__search_messages` with a distinctive substring of the subject.

## "Am I on Proton VPN right now?"

Just `vpn__status`. Report `connected`, `country`, and `org` to the user.

## Pre-flight before doing something privacy-sensitive

If the user is about to do something privacy-sensitive (download something, log in to a sensitive account):

1. `vpn__status` first.
2. If `connected: false`, warn the user and ask whether to proceed without VPN.

## Daily digest

1. `mail__get_unread`
2. `mail__list_folder_messages` with `folder="Sent"`, `limit=5` — what they sent
3. `vpn__status`
4. Optionally `drive__list` if they care about recent file changes

Compose a one-paragraph summary. Don't enumerate everything — call out highest-signal items.
