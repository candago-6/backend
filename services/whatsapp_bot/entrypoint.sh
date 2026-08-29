#!/bin/sh
set -e

# Chromium leaves SingletonLock/SingletonSocket/SingletonCookie behind when the
# container is killed without a graceful shutdown, which then blocks the next
# launch with "profile appears to be in use by another Chromium process".
find /app/.wwebjs_auth -iname 'Singleton*' -delete 2>/dev/null || true

# Run node as PID 1 so it receives SIGTERM directly: npm does not forward signals,
# which left Chromium to be SIGKILLed with a dirty profile and a broken session.
exec node index.js
