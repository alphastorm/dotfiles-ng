---
description: "Never claim a Discord or maintainer discussion the founder has not confirmed in public pull request text"
condition:
  - '(?i)\b(?:proposed|discussed|raised|floated|pitched|mentioned|posted|shared|agreed|approved|confirmed|cleared|checked|talked|announced)\b[^\n]{0,80}\b(?:on|in|via|over|through)\s+(?:the\s+)?(?:[\w-]+\s+)?discord\b'
  - '(?i)\b(?:per|after|following|as agreed in|as discussed in|as proposed in)\s+(?:the\s+|a\s+|our\s+)?(?:[\w-]+\s+)?discord\b'
  - '(?i)\bdiscord\b[^\n.]{0,40}\b(?:approved|agreed|sign-?off|go-?ahead|blessing|consensus)\b'
  - '(?i)\b(?:discussed|checked|cleared|agreed|confirmed)\b[^\n.]{0,20}\bwith (?:the |a |two |several )?maintainers?\b'
  - '(?i)\bmaintainers?\b[^\n.]{0,30}\b(?:agreed|approved|signed off|okayed|gave (?:the |their |a )?go-?ahead)\b'
scope: tool
interruptMode: always
---

Stop. Do not write that a change was proposed, discussed, or approved on Discord, or that maintainers agreed to it, unless the founder told you in this conversation that it happened. A claim already present in a pull request body, comment, commit, or earlier draft is not evidence: agent sessions write text under his account, and one invented "Proposed on Discord before implementation" for oh-my-pi #13689.

Upstream `CONTRIBUTING.md` asks for a Discord discussion before implementing major features or broad behavioral changes. When a change may need one and the founder has not confirmed it, leave the claim out and tell him in chat that the pull request has no Discord discussion; whether to start one is his call.

Rewrite the text without the claim. If the match was not a claim that a discussion happened (for example, quoting `CONTRIBUTING.md`'s requirement or asking the founder about one), continue as you were.
