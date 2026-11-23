---
description: صرّح بميزانية دفعة توليد (إذن صرف فقط وليس موافقة فنية)
argument-hint: <amount> "<scope>" [-p PROJECT_ID]
---

The human is authorizing spending. Arguments: $ARGUMENTS
Confirm the exact amount, scope (shots/phase/provider) and that this is spending permission only, not artistic approval. Then run
`python3 studio/studio.py cost authorize --amount <n> --scope "<scope>" --by "<human name>" [--phase design|proof|production]`.
Report the AU id and remaining budget (Arabic). Refuse if the amount exceeds the approved budget; tell the human to raise it first.
