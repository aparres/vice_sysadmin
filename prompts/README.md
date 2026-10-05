Version 1 templates are loaded by vice_sysadmin.prompts.PromptBook.
The auditor template and rendering code are identical for conditions B and C.
Hashes cover exact file bytes. Supply a deterministic nonce factory only in tests
or controlled prompt replay. Fresh random nonces are the normal default.
All embedded data stays between nonce delimiters; this mitigates injection but
does not establish a security boundary. See the project README for limitations.
