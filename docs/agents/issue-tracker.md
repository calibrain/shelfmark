# Issue Tracker: GitHub Issues and Discussions

GitHub is the shared source of truth for tracker work in this repository. Use the `gh` CLI from this checkout; it infers `muneebabbas/shelfmark` from `origin`.

## Conventions

- **GitHub Issues** hold actionable work: bugs, feature requests, specs, implementation tickets, and wayfinder maps.
- **GitHub Discussions** hold open-ended questions, design exploration, announcements, and community conversation that is not yet actionable work.
- Create an Issue when it has a clear outcome, owner, or lifecycle. Start a Discussion when the goal is to gather perspectives; create linked Issues for work that emerges.
- Use the canonical triage labels in `triage-labels.md` to show an Issue's state.
- Link related Issues and Discussions rather than duplicating their decisions in repository files.

## GitHub Issue Operations

- **Create**: `gh issue create --title "..." --body "..."`
- **Read**: `gh issue view <number> --comments`
- **List**: `gh issue list --state open`
- **Comment**: `gh issue comment <number> --body "..."`
- **Label**: `gh issue edit <number> --add-label "..."` or `--remove-label "..."`
- **Close**: `gh issue close <number> --comment "..."`

## GitHub Discussion Operations

- Use the GitHub web UI for creating and categorizing Discussions.
- Use `gh api graphql` to read or automate Discussion operations when the web UI is insufficient.
- When a Discussion produces actionable work, create an Issue that links back to the Discussion and records the agreed outcome.

## When a Skill Says "Publish to the Issue Tracker"

Create a GitHub Issue. Publish exploratory conversation as a GitHub Discussion only when it is not yet actionable.

## When a Skill Says "Fetch the Relevant Ticket"

Run `gh issue view <number> --comments`. For a Discussion, use its GitHub URL or query it with `gh api graphql`.

## Wayfinding Operations

Used by `/wayfinder`. The **map** is one GitHub Issue with child Issues as tickets. Discussions may inform a map, but are not map tickets.

- **Map**: create one Issue labelled `wayfinder:map`, containing the Destination, Notes, Decisions so far, Not yet specified, and Out of scope sections.
- **Child ticket**: create an Issue labelled `wayfinder:<type>` (`research`, `prototype`, `grilling`, or `task`) and add it as a GitHub sub-issue of the map. If sub-issues are unavailable, add it to a task list in the map and put `Part of #<map>` in the child body.
- **Blocking**: use GitHub's native issue dependencies. If unavailable, put `Blocked by: #<number>, #<number>` at the top of the child body.
- **Frontier**: choose the first open map child that has no open blockers and no assignee.
- **Claim**: assign the ticket to the driving developer with `gh issue edit <number> --add-assignee @me` before doing work.
- **Resolve**: post the answer as an Issue comment, close the ticket, then add a linked one-line gist to the map's Decisions so far.
