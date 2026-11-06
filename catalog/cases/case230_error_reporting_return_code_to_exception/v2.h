#ifndef CASE230_H
#define CASE230_H

namespace cfg {
/* Parses a TCP port number from text.
 * Contract (documentation only): returns -1 when the text is not a valid port. */
int parse_port(const char *text);
}  // namespace cfg

#endif
