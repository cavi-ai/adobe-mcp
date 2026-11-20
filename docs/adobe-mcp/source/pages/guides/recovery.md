# Recovery and unknown outcomes

The servers do not retry. Before a tool dispatches a native command it records the operation. When the command
finishes and its result is verified, the record is resolved. When the command may have run but the result is
missing, late or wrong, the outcome is unknown: the record stays, `retry_safe` is `false`, and the next change is
refused until the record is resolved.

## What causes an unknown outcome

- A command timed out (45 seconds for Photoshop and Illustrator, 120 for the generative tools, 60 for Animate, 30 for
  Dimension, 60 for starting a Dimension render).
- The application reported an error after it had started changing the document.
- The application process changed during the command.
- A receipt did not match the command, or a file changed during it.
- Photoshop showed a prompt that was not answered, such as the Generative Credits notice.

A request refused before dispatch is a rejection, not an unknown outcome. Nothing was changed and you can correct the
call and send it again.

## Photoshop and Illustrator

1. Look at the application and at the output files. Clear any prompt by hand.
2. `adobe_inspect` with the `application` shows `pending_operation`. A pending operation blocks every change.
3. `adobe_operation_control` with the `session_id`, the `operation_id` and `action` `inspect` returns:

   | Field | Meaning |
   |---|---|
   | `completion_available` | The application wrote a native receipt for the command. |
   | `can_reconcile` | The same process still has the saved owned document and the source is unchanged. |
   | `copy_sha256`, `native_sha256` | Hashes of the copy on disk and of what the application shows. |
   | `document` | What the application shows for the copy. |

4. If `can_reconcile` is `true` and the document is what you expect, call again with `action` `adopt_current` and both
   `expected_copy_sha256` and `expected_native_sha256` from step 3. If either has changed, the call is refused and you
   inspect again. The state becomes `adopted_current`.
5. If the application has quit, `action` `abandon_after_exit` abandons the operation (`abandoned_after_process_exit`).
   It is refused while the original process is running.

An unknown read (`adobe_inspect`) is cleared by a later successful inspect in the same process.

## Animate

`animate_operation_control` works the same way with the `session_id` and `operation_id` and these actions:

| `action` | Use |
|---|---|
| `inspect` | Report `completion_available`, `can_reconcile`, `copy_sha256`, `native_sha256` and `document`. |
| `adopt_current` | Accept the observed document. Needs both expected hashes from `inspect`. |
| `cancel_unsubmitted` | Drop an operation that was never dispatched to Animate. |
| `abandon_after_exit` | Drop the operation after Animate has quit. |

`animate_inspect` shows `pending_operation`. The record is in `.animate-authoring/pending.json`.

## Dimension

A render that did not report back is resolved with `dimension_render_inspect` and the `operation_id` from the error
message. It reads the same job: `pending` until the PNG exists, then the receipt. Do not start another render until
the job has a result. There is no cancel action.

## Rules

- Do not delete a pending record by hand: it is the only account of what may have happened.
- Do not repeat a command to see whether it works. A repeated Photoshop generative call spends credits again.
