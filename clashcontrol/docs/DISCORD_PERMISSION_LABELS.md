# Discord permission labels — which wording to use where

Verified from Discord Windows client screenshots by qaplop on 2026-10-11 (tracker #0151).
Rule (../../.github/copilot-instructions.md Cardinal Rule 6): permission names in user-facing text are
quoted and use Discord's exact wording for the place the reader is looking at. Latin readers see
Discord in English (Discord has no Latin client), so Latin uses the English labels.

Discord shows **three different sets** of names for the same permissions:

1. **Server Settings → Roles → (role) → Permissions** — the client's role settings. This is what
   an admin looks at when fixing a permission, so it is what the bot uses by default
   (`permissions.perm.*` in the translations, rendered by `bot_permissions.perm_label()`).
2. **The authorize dialog** ("Confirm that you want to grant ClashControl the following
   permissions…") — shown when installing the bot or clicking /setup's "Re-authorize" link. Short,
   older names. Use these ONLY in a text that tells the reader what that dialog shows. No text
   does so today.
3. **Developer Portal → Installation → Default Install Settings** — the permission picker for the
   install link (the README's install list uses these English names). The German picker differs
   from both sets above (e.g. „Soforteinladung erstellen“); never use it as a reference for
   user-facing text.

Not yet verified: a channel's permission overrides (Edit Channel → Permissions), where the view
permission is expected to read "View Channel" (singular) — `permissions.perm.view_channel`; the
role-level plural lives in `permissions.perm_server.view_channel`.

## 1. Role settings (used by the bot)

| Permission (API) | en | de | es | zh |
|---|---|---|---|---|
| administrator | Administrator | Administrator | Administrador | 管理员 |
| view_channel (server) | View Channels | Kanäle ansehen | Ver canales | 查看频道 |
| manage_roles | Manage Roles | Rollen verwalten | Gestionar roles | 管理身份组 |
| create_instant_invite | Create Invite | Einladung erstellen | Crear invitación | 创建邀请 |
| send_messages | Send Messages and Create Posts | Nachrichten versenden und Posts erstellen | Enviar mensajes y crear publicaciones | 发送消息与创建帖子 |
| send_messages_in_threads | Send Messages in Threads and Posts | Nachrichten in Threads und Posts senden | Enviar mensajes en hilos y publicaciones | 在子区和帖子中发送消息 |
| create_public_threads | Create Public Threads | Öffentliche Threads erstellen | Crear hilos públicos | 创建公共子区 |
| manage_threads | Manage Threads and Posts | Threads und Posts verwalten | Gestionar hilos y publicaciones | 管理子区和帖子 |
| embed_links | Embed Links | Links einbetten | Insertar enlaces | 嵌入链接 |
| attach_files | Attach Files | Dateien anhängen | Adjuntar archivos | 添加附件 |
| read_message_history | Read Message History | Nachrichtenverlauf anzeigen | Leer el historial de mensajes | 阅读消息历史 |
| use_external_emojis | Use External Emojis | Externe Emojis verwenden | Usar emojis externos | 使用外部表情符号 |
| manage_guild | Manage Server (not verified) | Server verwalten (not verified) | Gestionar servidor | 管理服务器 (not verified) |

## 2. Authorize dialog (reference only)

| Permission (API) | en | de | es | zh |
|---|---|---|---|---|
| administrator | Administrator | Administrator | Administrador | 管理员 |
| manage_roles | Manage Roles | Rollen verwalten | Gestionar roles | 管理身份组 |
| create_instant_invite | Create Invite | Einladung erstellen | Crear invitación | 创建邀请 |
| view_channel | View Channels | Kanäle ansehen | Ver canales | 查看频道 |
| send_messages | Send Messages | Nachrichten senden | Enviar mensajes | 发送消息 |
| send_messages_in_threads | Send Messages in Threads | Nachrichten in Threads senden | Enviar mensajes en los hilos | 在子区内发送消息 |
| create_public_threads | Create Public Threads | Öffentliche Threads erstellen | Crear hilos públicos | 创建公共子区 |
| manage_threads | Manage Threads | Threads verwalten | Gestionar hilos | 管理子区 |
| embed_links | Embed Links | Links einbetten | Insertar enlaces | 嵌入链接 |
| attach_files | Attach Files | Dateien anhängen | Adjuntar archivos | 添加附件 |
| read_message_history | Read Message History | Nachrichtenverlauf anzeigen | Leer el historial de mensajes | 阅读消息历史 |
| use_external_emojis | Use External Emojis | Externe Emojis verwenden | Usar emojis externos | 使用外部表情符号 |
