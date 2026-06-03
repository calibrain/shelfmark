# 📚 Pulsarr

> A fork of [calibrain/shelfmark](https://github.com/calibrain/shelfmark) 
> that adds automated author/series monitoring and new release detection.
> Named after a pulsar — a celestial object that emits regular periodic 
> pulses — reflecting Pulsarr's core feature of periodically checking for 
> new releases from your watched authors.
>
> The original Shelfmark project is stable but not under active maintenance 
> and explicitly does not include automation features. Pulsarr adds those 
> features while staying as close to upstream as possible to allow pulling 
> bug fixes.

## ✨ What Pulsarr Adds

- **Author & Series Watchlist** — Add authors and series to monitor automatically
- **New Release Detection** — Periodic checks against Hardcover and Open Library for new releases from watched authors
- **Auto-Queue** — Automatically push new releases into the download queue
- **Release Calendar** — View upcoming and recently detected releases
- **Configurable Schedule** — Set how often to check for new releases
- **Notifications** — Get alerted when new releases are found or downloaded

## 🔄 Staying Up To Date With Upstream

Pulsarr tracks `calibrain/shelfmark` main branch. To pull upstream fixes:

```bash
git remote add upstream https://github.com/calibrain/shelfmark.git
git fetch upstream
git merge upstream/main
```

Monitoring-specific code is isolated to avoid merge conflicts where possible.