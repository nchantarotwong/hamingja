# compiler_language

**Optional profile — opt in only for compiler/language/runtime work.** The
phase model below is wrong for ordinary application code.

Compiler and language work proceeds by phase:

1. **grammar / parser** — surface syntax → token stream → parse tree
2. **AST** — typed tree representation, desugaring
3. **semantic analysis / type checking** — name resolution, type inference, validity rules
4. **lowering / IR / codegen** — transform to lower-level form, emit target code
5. **runtime behavior** — execution semantics, garbage collection, FFI, intrinsics
6. **diagnostics** — error/warning messages, source spans, suggestions
7. **docs / examples** — language reference, tutorials, test programs

Before editing in response to a failure, classify the **phase** in which it manifests, and the phase in which the cause likely lives. They are often different — a runtime crash can originate in lowering; a type error can originate in parsing.

Discipline:
- Do **not** modify multiple phases in one attempt unless explicitly approved. A cross-phase edit is a re-design, not a fix; treat it as one.
- If you cannot explain the AST, IR, or runtime state at the failure point, you do not yet have a hypothesis — you have a guess. Inspect that state (print the AST, dump the IR, log the runtime values) before changing code.
- A repro that goes "source → expected vs actual output" is not enough. State the expected intermediate forms at each phase boundary the change touches, and check them.

Escalate (in addition to the general `escalation` rules) when:
- the failure crosses compiler phases (e.g. parser change broke codegen, type rule change broke a runtime intrinsic)
- you cannot explain the AST/IR/runtime state at the failure point after a deliberate inspection
- the change touches the type system, evaluation order, memory model, or ABI — these are language semantics, where mistakes compound across every program ever written in the language
