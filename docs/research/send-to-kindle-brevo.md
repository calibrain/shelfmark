# Send-to-Kindle and Brevo SMTP Research

Research date: 2026-07-26. This note inspects the current source tree only; `shelfmark/.env` was not read.

## Shelfmark delivery configuration

Send-to-Kindle posts an attached completed file through Shelfmark's shared SMTP helper. It requires a library member's owned file and a per-user Kindle recipient; SMTP/configuration failures return HTTP 500. [Source: `shelfmark/core/library_routes.py:474-568`; `shelfmark/download/outputs/email.py:252-305`]

| Variable | Default | Requirement and current behavior |
| --- | --- | --- |
| `EMAIL_SMTP_HOST` | empty | Required. Empty/whitespace raises `SMTP host is required`. [Source: `shelfmark/download/outputs/email.py:67-89,127`] |
| `EMAIL_SMTP_PORT` | `587` | Must be an integer >= 1. [Source: `shelfmark/download/outputs/email.py:49-64,70,128`] |
| `EMAIL_SMTP_SECURITY` | `starttls` | Must be exactly `none`, `starttls`, or `ssl` (case-insensitive after trimming). `starttls` calls SMTP `STARTTLS`; `ssl` opens SMTP-over-SSL. [Source: `shelfmark/download/outputs/email.py:24-27,72-75,153-178`] |
| `EMAIL_SMTP_USERNAME` | empty | Optional in Shelfmark, but a password becomes required when supplied. Brevo requires its SMTP login email here. [Source: `shelfmark/download/outputs/email.py:77-78,90-92`; [Brevo SMTP guide](https://help.brevo.com/hc/en-us/articles/7924908994450-Send-transactional-emails-using-Brevo-SMTP)] |
| `EMAIL_SMTP_PASSWORD` | empty | Required when `EMAIL_SMTP_USERNAME` is set. For Brevo this must be an SMTP key, not an API key. [Source: `shelfmark/download/outputs/email.py:77-78,90-92`; [Brevo SMTP guide](https://help.brevo.com/hc/en-us/articles/7924908994450-Send-transactional-emails-using-Brevo-SMTP)] |
| `EMAIL_FROM` | empty | Required unless `EMAIL_SMTP_USERNAME` parses as an email address, in which case Shelfmark uses `Shelfmark <username>`. A configured value is used unchanged as the message `From` header. For Brevo, use a registered/verified sender. [Source: `shelfmark/download/outputs/email.py:80,94-112,285-292`; [Brevo sender guide](https://help.brevo.com/hc/en-us/articles/208836149-Create-a-new-sender-From-name-and-From-email)] |
| `EMAIL_SMTP_TIMEOUT_SECONDS` | `60` | Must be an integer >= 1; used for SMTP connections. [Source: `shelfmark/download/outputs/email.py:49-64,82-84,110,127-135`] |
| `EMAIL_ALLOW_UNVERIFIED_TLS` | `false` | Boolean strings `true`, `1`, `yes`, and `on` disable certificate and hostname verification. Do not enable for Brevo in normal operation. [Source: `shelfmark/download/outputs/email.py:122-135,145-150`] |
| `EMAIL_SUBJECT_TEMPLATE` | `{Title}` | Supported shared-mail setting, but not effective for Send-to-Kindle: this path explicitly uses the attachment filename as subject. [Source: `shelfmark/download/outputs/email.py:81,112,133,252-258,285-290`] |

The SMTP helper checks environment variables before its `core_config.config` fallback, so all SMTP transport settings in this table can be supplied entirely through process/container environment. [Source: `shelfmark/download/outputs/email.py:116-136`]

## Required combination

For a Brevo-backed Send-to-Kindle request to reach SMTP authentication, set all of the following:

1. `EMAIL_SMTP_HOST`, `EMAIL_SMTP_PORT`, `EMAIL_SMTP_SECURITY`, `EMAIL_SMTP_USERNAME`, and `EMAIL_SMTP_PASSWORD`.
2. `EMAIL_FROM`, unless the username is a valid email address and the implicit `Shelfmark <username>` From value is acceptable. Explicit `EMAIL_FROM` is recommended because Brevo expects a configured sender. [Source: `shelfmark/download/outputs/email.py:94-101`; [Brevo SMTP guide](https://help.brevo.com/hc/en-us/articles/7924908994450-Send-transactional-emails-using-Brevo-SMTP)]
3. A Kindle recipient in the acting user's `kindle_address` preference, plus an owned completed file in that user's library. These are endpoint prerequisites, not environment variables. [Source: `shelfmark/core/library_routes.py:483-541`]

## Brevo requirements and mapping

Brevo's official SMTP documentation specifies `smtp-relay.brevo.com`, the account's SMTP login email, and an SMTP key. It explicitly says not to use an API key. Brevo recommends TLS on port 587; port 2525 is an alternative if 587 is blocked; port 465 is for SSL. [Brevo SMTP guide](https://help.brevo.com/hc/en-us/articles/7924908994450-Send-transactional-emails-using-Brevo-SMTP); [Brevo port guide](https://help.brevo.com/hc/en-us/articles/10905415650322-Which-SMTP-port-should-I-use-Port-587-465-or-2525)]

Create the SMTP key under Brevo **Settings > SMTP & API > SMTP**. Brevo describes the SMTP login as the username and an SMTP key as the password; the complete key is displayed only once. [Brevo SMTP-key guide](https://help.brevo.com/hc/en-us/articles/7959631848850-Create-and-manage-your-SMTP-keys)

Before sending, add a sender in Brevo. Brevo recommends authenticating the sender domain; otherwise it requires verification using the six-digit code sent to the sender address. [Brevo sender guide](https://help.brevo.com/hc/en-us/articles/208836149-Create-a-new-sender-From-name-and-From-email)]

Amazon also requires the exact `EMAIL_FROM` address to be included in the Kindle account's Approved Personal Document Email List. Amazon permits up to 15 approved sender addresses and accepts a total attachment size of 50 MB or less. [Amazon Send to Kindle email help](https://www.amazon.com/gp/help/customer/display.html?nodeId=G7NECT4B4ZWHQ8WV)

## Sanitized Brevo `.env` example

```dotenv
# Brevo SMTP relay (TLS via STARTTLS)
EMAIL_SMTP_HOST=smtp-relay.brevo.com
EMAIL_SMTP_PORT=587
EMAIL_SMTP_SECURITY=starttls
EMAIL_SMTP_USERNAME=your-brevo-smtp-login@example.com
EMAIL_SMTP_PASSWORD=replace-with-brevo-smtp-key
EMAIL_FROM="Shelfmark <verified-sender@example.com>"

# Optional Shelfmark transport settings
EMAIL_SMTP_TIMEOUT_SECONDS=60
EMAIL_ALLOW_UNVERIFIED_TLS=false
```

For Brevo port 465, use `EMAIL_SMTP_PORT=465` and `EMAIL_SMTP_SECURITY=ssl`. Do not use `EMAIL_SMTP_SECURITY=none` for Brevo. [Source: `shelfmark/download/outputs/email.py:24-27,153-178`; [Brevo port guide](https://help.brevo.com/hc/en-us/articles/10905415650322-Which-SMTP-port-should-I-use-Port-587-465-or-2525)]

Each user must save their Send-to-Kindle recipient through My Settings or `PUT /api/users/me`; Shelfmark persists it as `kindle_address` in `user_preferences`. Therefore environment variables alone do **not** complete Send-to-Kindle setup. [Source: `src/frontend/src/components/settings/SelfSettingsModal.tsx`; `shelfmark/core/self_user_routes.py:24-29,118-191`; `shelfmark/core/user_db.py:611-668`]
