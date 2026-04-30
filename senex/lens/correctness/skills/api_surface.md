## HIGH FAN-IN / API SURFACE FOCUS

This symbol is called by many callers (high graph fan-in) or is a public endpoint.
Elevate scrutiny on:
- Contract stability: changing the signature or raising new exceptions is a
  breaking change for all callers — note this explicitly in findings
- Input validation: untrusted data enters the system here; validate before use
- Error handling: errors must not leak internal state to callers
- Authentication / authorization: is every code path gated appropriately?
- Rate limiting and resource bounds: can a caller trigger unbounded work?
