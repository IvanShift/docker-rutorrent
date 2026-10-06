"""Verify every produced torrent against independent payload SHA1 pieces."""
import hashlib
import re
import sys
from pathlib import Path

root = Path(sys.argv[1])

def decode(data):
    def parse(i):
        token = data[i:i + 1]
        if token == b"i":
            end = data.index(b"e", i)
            return int(data[i + 1:end]), end + 1
        if token in (b"l", b"d"):
            result = [] if token == b"l" else {}
            i += 1
            while data[i:i + 1] != b"e":
                value, i = parse(i)
                if token == b"l":
                    result.append(value)
                else:
                    key = value
                    value, i = parse(i)
                    assert key not in result, "duplicate dictionary key"
                    result[key] = value
            return result, i + 1
        colon = data.index(b":", i)
        size = int(data[i:colon])
        start, end = colon + 1, colon + 1 + size
        assert size >= 0 and end <= len(data), "invalid string size"
        return data[start:end], end
    result, end = parse(0)
    assert end == len(data), "trailing torrent bytes"
    return result

verified = 0
for torrent in sorted(root.glob("*.torrent")):
    _, payload_name, control, _ = torrent.stem.split("-")
    payload = (root / payload_name).read_bytes()
    info = decode(torrent.read_bytes())[b"info"]
    piece_size = 1 << 18
    expected = b"".join(hashlib.sha1(payload[i:i + piece_size]).digest()
                        for i in range(0, len(payload), piece_size))
    assert info[b"length"] == len(payload), torrent.name
    assert info[b"piece length"] == piece_size, torrent.name
    assert info[b"pieces"] == expected, torrent.name
    output = torrent.with_suffix(".torrent.log").read_text()
    threads = re.search(r"Threads:\s+(\d+)\b", output)
    assert threads and int(threads[1]) >= 1, torrent.name
    if control == "single":
        assert threads[1] == "1", torrent.name
    verified += 1
assert verified == 64, ("missing successful trials", verified)
print("ok - mktorrent64 file/pipe/default/single/multiple-piece trials; all independent SHA1 pieces match")
