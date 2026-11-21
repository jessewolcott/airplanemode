# 0007. One sign-in for everyone: NAT plus captive-portal detection

Date: 2026-11-21 · Status: accepted

## Context

Hotel, airport and in-flight Wi-Fi usually put a sign-in page ("captive portal") in front of
the internet, often per device and sometimes with a device limit. Behind AirplaneMode, the
user's devices can't see that page directly: their own "are we online?" checks go through
the router and may succeed or fail in confusing ways.

## Decision

- The box joins the upstream network as one client and **NATs** your hotspot behind it, so
  the venue sees **one device**. Once anyone signs in through it, everyone behind it is online.
- The admin service **probes** `http://connectivitycheck.gstatic.com/generate_204`, the
  same kind of check phones use:
  - `204` means online;
  - a redirect means a sign-in page (we keep its `Location` as the link to open);
  - any other page is treated as a sign-in page too (the link is the probe URL itself,
    which the venue redirects);
  - no answer means offline.

  Results are cached for 10 s.
- The landing page shows the state to **everyone**, not just admins. Signing in is the
  everyday case, and any guest on the hotspot can do it: **Open the sign-in page**, then
  **I've signed in** to re-check.
- For venues that tie access to one device, the admin panel can **clone a MAC address** onto
  the upstream interface: sign in on a phone first, then make the box look like that phone.

## Consequences

- The probe goes to a Google URL whenever the landing page is open (one request per 10 s at
  most). `PROBE_URL` can point elsewhere.
- Portals that need JavaScript-heavy flows still work. The user completes them in their own
  browser, through the box.
- Some venues forbid sharing. Respect their terms. The feature makes sharing easy, it
  doesn't hide it.
