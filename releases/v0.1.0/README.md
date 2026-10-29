# Temple Signal 0.1.0 application disc

`TEMPLEOS_SIGNAL.ISO` is a **nonbootable RedSea application disc** containing the
HolyC source, its tests, a short guest readme and the GPL license. Boot the
original TempleOS ISO separately, then attach this disc as the second IDE CD.
It contains neither a modified TempleOS operating system nor a Signal account.

The host bridge remains in the repository's `temple_signal/` directory. Use the
[setup guide](../../docs/SETUP.md) to start it and load `U:/Signal/Load.HC`.

The image is reproducible from this repository:

```sh
python3 scripts/build_media.py --output build/TEMPLEOS_SIGNAL.ISO
```

Compare its SHA-256 with `SHA256SUMS` in this folder. The image's fixed timestamp
is a reproducibility choice, not the build date.
