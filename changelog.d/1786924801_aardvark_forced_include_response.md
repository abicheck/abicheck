### Security

- **Blocked response-file injection through derived forced includes.** Build evidence containing an `@response-file` forced-include operand is no longer forwarded into the Clang or CastXML header parse, preventing the response file from injecting compiler flags.
