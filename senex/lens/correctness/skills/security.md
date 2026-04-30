## SECURITY FOCUS

This file has security-sensitive characteristics. Elevate scrutiny on:
- Input validation at trust boundaries (user-supplied paths, query params, headers)
- Credential handling: secrets must not appear in logs, exceptions, or responses
- Cryptography: flag weak algorithms (MD5/SHA1 for integrity, ECB mode, short keys)
- Injection surfaces: SQL, shell, LDAP, XPath, template injection
- Unsafe deserialization: native object deserializers on untrusted input can yield RCE
- SSRF: outbound requests constructed from user input

Tag each security finding with the closest CWE ID.
