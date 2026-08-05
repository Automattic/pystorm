# pystorm-a8c

Slim Apache Storm multi-lang runtime, topology DSL, and Nimbus submit client
for Parse.ly.

This package replaces the two Automattic forks `pystorm` and `streamparse`
with a single distribution containing only the codepaths that are actually
used: the runtime component classes, the topology DSL, a `submit` command that
talks directly to Nimbus over Thrift, and a `jar` command that assembles the
topology archive with the standard library.

Target: **Apache Storm 1.2.x**. One runtime dependency: `thriftpy2`.

See `doc/plans/2026-08-05-pystorm-a8c.md` for the full design and rationale.
