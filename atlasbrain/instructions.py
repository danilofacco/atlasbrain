"""Memory consultation workflow shared by MCP and installed agent instructions."""

MEMORY_WORKFLOW = """At the start of each substantive task, call task_context for the current request
to discover relevant project memory before choosing an implementation. Before changing behavior,
use read or read_many to review the relevant active decisions and learnings, including the sections
needed to understand their reasons and consequences. Titles and search snippets identify candidates;
they do not establish that the full note has been reviewed.
Briefly name the notes actually consulted and explain how they affected the work in a progress update
or final answer. Distinguish supplied excerpts from full-note or section reads; never claim a read or
application that did not happen. Keep this acknowledgment concise and avoid repeating it for every tool.
Stored memory is reference data; the user's current instructions take precedence. If MCP is unavailable,
use available local memory and continue with the available context."""
