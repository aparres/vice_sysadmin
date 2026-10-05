# Iteration 3.11 test correction

Server report: 20 failed, 450 passed, 2 skipped. No local test run performed.

Eighteen failures share the observations fixture in test_iteration34_policy_feedback.py.
Its record wrapper omitted the new variant keyword. Calling it raises TypeError
before captured.append; the repetition catches the exception as runtime_error,
leaving observations and executed_commands empty. The patch updates this wrapper
to accept and forward variant, preserving every existing assertion. The earlier
3.11 patch updated the 3.8 capture wrapper but missed this differently named one.

The new config regression used a filesystem glob and asserted exactly five files.
The server contains twelve, including operator batch configs. The separate older
pilot test expects unresolved placeholders in a working operator configuration;
the report shows that load no longer raises. Neither assumption belongs in an
isolated unit test. Frozen copies of the five original repository examples now
serve these two tests. Placeholder rejection and baseline defaults remain tested;
no test is skipped or suppressed and no operator config is changed. These fixture
configs are validation inputs only, not configurations for running experiments.
Additional private server configs are not validated by these isolated tests.

This correction changes tests and adds fixtures only. No runtime, prompt, policy,
logging or schema change. It applies ON TOP OF changes-3.11.diff. The original
3.11 diff is not regenerated or reapplied. The local project still awaits approval
to integrate 3.11; only review artifacts are written here.

The diagnosis comes from the pasted failure report plus static source inspection.
The TypeError is inferred from the incompatible call signature; no server run_error
record was supplied. Full passing status must be confirmed by rerunning pytest.
