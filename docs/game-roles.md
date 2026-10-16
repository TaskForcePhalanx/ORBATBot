<!-- Part of ORBATBot's documentation. The rules every session needs
     are in ../CLAUDE.md, which says when to read this file. -->

## Game Roles (`cogs/gameroles.py`)

Self-assignable cosmetic roles for games (Minecraft, DCS, …), separate from the slot system. Members opt in themselves; the roles exist purely so people can @mention everyone who plays a given game.

### Guarantee: no permissions

Roles the bot creates are created with `discord.Permissions.none()` and `mentionable=True`. Registering a pre-existing role runs `_role_rejection()`, which refuses the role if it:
- grants any permission at all (`role.permissions.value != 0`)
- is `@everyone`, or is `managed` (bot/integration/booster role)
- is a unit role or `Unit Leader` (`PROTECTED_ROLE_NAMES`)
- sits at or above the bot's top role, which Discord won't let the bot assign

### Commands

**`/game-role-add <name> [emoji] [description]`** (Admin)
Creates a permission-free, mentionable role named `name` and registers it. If a role with that **exact name** already exists it is reused instead of duplicated, after passing `_role_rejection()`. Re-running with the same name updates the emoji/description in place. Capped at 25 roles per guild (Discord's select-menu limit). Refreshes the panel.

**`/game-role-remove <role> [delete_role]`** (Admin)
Unregisters the role. By default the Discord role survives and members keep it; `delete_role: True` deletes it outright. Refreshes the panel.

**`/game-role-list`** (everyone)
Ephemeral list of every registered game role.

**`/game-role-panel [channel]`** (Admin)
Posts the persistent panel: an embed listing the roles plus a **🎮 Choose your game roles** button. One panel per guild (`game_role_panels`, keyed on `guild_id`); it self-updates whenever a role is added or removed via `_update_game_role_panel()`.

**`/game-roles`** (everyone)
Opens the picker directly — same flow as the panel button.

### Self-assign flow

The panel button and `/game-roles` both call `_send_role_picker()`, which sends an ephemeral `GameRoleSelectView`: one multi-select listing every game role, with the member's current roles **pre-ticked** (`SelectOption.default`) and `min_values=0` so deselecting everything is a valid submission.

On submit the member's game roles are set to exactly what's ticked — the view diffs the selection against their current roles and issues only the needed `add_roles` / `remove_roles` calls, then reports what changed. Selecting no change makes no API calls at all.

**Removal has two paths.** Unticking an option in that select already removes the role, but deselecting a pre-ticked option is easy to miss in Discord's UI, so the picker also carries a **➖ Remove a role** button — added only when the member holds at least one game role. It swaps in `GameRoleRemoveView`: a select listing *only* the roles they currently hold, `min_values=1`, nothing pre-ticked, so selecting is unambiguously "remove this". Both views share `_apply_role_changes()` (add/remove plus Forbidden/HTTPException handling) and `_change_summary()`.

Both paths re-check `interaction.user.roles` at submit time, so a role removed by someone else in the meantime results in a no-op with an explanation rather than a failed API call.

### Notes for future changes

- **Persistence:** `GameRolePanelView` has one static `custom_id` (`game_roles_open`) and is registered once in `setup_hook()`, so the panel button keeps working after a restart with no per-guild bookkeeping. This is why the panel is a button opening an ephemeral picker rather than a select menu directly on the public message — a shared message can't pre-tick per-viewer state.
- **No new intents.** `interaction.user.roles` comes from the interaction payload and role add/remove are REST calls, so `Intents.default()` still suffices. Member *counts* are deliberately not displayed anywhere: without the privileged members intent `role.members` is unreliable.
- **Emoji validation:** `_is_renderable_emoji()` guards both input and render. `PartialEmoji.from_str()` silently accepts plain text like `"minecraft"`, and Discord then rejects the whole component — which would break the picker for everyone — so anything containing ASCII letters is rejected on input and skipped at render time.
- **`guild_only` is applied per command, not on the cog class.** A class-level `@app_commands.guild_only()` is only honoured on `commands.GroupCog`; on a plain `Cog` it is silently ignored (see `ext/commands/cog.py`, the `__cog_is_app_commands_group__` branch).
- Registrations whose Discord role was deleted are pruned from the DB by `_resolve_game_roles()` on next read.

---
