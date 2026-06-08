# Stale Info Hash XMLRPC Faults Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop ruTorrent from issuing noisy or UI-breaking detail RPC calls against torrent hashes that were already removed/replaced in rTorrent.

**Architecture:** Treat `invalid parameters: info-hash not found` as a stale-selection race, not as a torrent replacement failure. Clear stale UI detail selection before detail refreshes run, make `httprpc` return empty detail payloads for removed torrents, and make `rutracker_check` skip state reads/writes for hashes that no longer exist.

**Tech Stack:** ruTorrent JavaScript overrides, PHP 8.5, rTorrent XMLRPC, `httprpc`, `rutracker_check`.

---

## Evidence From Attached Log

The pasted log repeatedly shows valid XMLRPC requests that rTorrent rejects with:

```text
faultCode: -500
faultString: invalid parameters: info-hash not found
```

The failed methods are detail/state calls for a single torrent hash:

- `f.multicall` for file list details.
- `p.multicall` for peer details.
- `t.multicall` for tracker details.
- `system.multicall` with `d.custom` / `d.custom1` for `rutracker_check` fields.

This matches a stale hash after torrent replacement: `rutracker_check::createTorrent()` loads an updated torrent whose info hash differs, then erases the old hash. During the next UI polling cycle, ruTorrent can still have the old hash in `theWebUI.dID` and request details for it.

## Root Cause

1. `rutracker_check::createTorrent()` in `overrides/rutorrent/plugins/rutracker_check/check.php` intentionally removes the old hash after loading the updated torrent. This is expected behavior when the torrent info hash changes.
2. Upstream `webui.js` refreshes active detail tabs before it removes stale torrent data from the UI cache. In that window it calls `updateTrackers(theWebUI.dID)` and `updatePeers(theWebUI.dID)` without checking whether `theWebUI.dID` exists in the new torrent list.
3. `overrides/rutorrent/plugins/httprpc/action.php` already returns an empty array for stale `fls` and `prs`, but `trk` still returns `false`.
4. `ruTrackerChecker::getState()` reads `d.custom` values for a hash without the existence guard that already exists in `setState()`.

## Files

- Modify: `overrides/rutorrent/js/common.js`
- Modify: `overrides/rutorrent/plugins/httprpc/action.php`
- Modify: `overrides/rutorrent/plugins/rutracker_check/check.php`
- Verify: `overrides/rutorrent/php/xmlrpc.php`

## Task 1: Clear Stale UI Detail Selection Before Refresh

**Files:**
- Modify: `overrides/rutorrent/js/common.js`

- [ ] **Step 1: Add a stale `dID` guard around `theWebUI.addTorrents`**

Add this block after the existing `patchListResponse()` block and before `patchGetTrackersResponse()`:

```js
// Clear selected details when the selected torrent disappeared from the latest list.
(function patchStaleDetailsSelection() {
	if (typeof $ === 'undefined')
		return;
	$(function () {
		if (!window.theWebUI || typeof theWebUI.addTorrents !== 'function' || theWebUI._staleDetailsSelectionPatched)
			return;

		const original = theWebUI.addTorrents;
		theWebUI.addTorrents = function (data) {
			const torrents = data && data.torrents;
			if (torrents && this.dID && !Object.prototype.hasOwnProperty.call(torrents, this.dID)) {
				delete this.files[this.dID];
				delete this.dirs[this.dID];
				delete this.peers[this.dID];
				delete this.trackers[this.dID];
				this.dID = "";
				this.clearDetails();
			}
			return original.call(this, data);
		};

		theWebUI._staleDetailsSelectionPatched = true;
	});
})();
```

Why this location: `common.js` already contains compatibility monkey patches, and this avoids creating a full `webui.js` override just to change one race.

- [ ] **Step 2: Validate JavaScript syntax**

Run:

```bash
node --check overrides/rutorrent/js/common.js
```

Expected: command exits `0` and prints no syntax error.

- [ ] **Step 3: Browser behavior check**

1. Open ruTorrent.
2. Select a torrent whose update changes info hash.
3. Keep the Tracker or Peer tab active.
4. Trigger `rutracker_check` replacement.

Expected: the details panel clears when the old hash disappears; the browser does not request `gettrackers`, `getpeers`, or `getfiles` for the old hash after the next list refresh.

## Task 2: Return Empty Tracker Details For Removed Torrents

**Files:**
- Modify: `overrides/rutorrent/plugins/httprpc/action.php`

- [ ] **Step 1: Make `trk` match `fls` and `prs` stale-hash behavior**

Change the `case "trk"` block to normalize a failed tracker multicall to an empty array:

```php
	case "trk":	/**/
	{
		$result = makeMulticall(array(
		        "t.get_url=", "t.get_type=", "t.is_enabled=", "t.get_group=", "t.get_scrape_complete=",
			"t.get_scrape_incomplete=", "t.get_scrape_downloaded=",
			"t.get_normal_interval=", "t.get_scrape_time_last="
			),$hash[0],$add,'t',$mode);
		// Graceful handling when torrent was deleted (info-hash not found)
		if($result === false)
			$result = array();
		break;
	}
```

- [ ] **Step 2: Make per-hash `trkall` values arrays**

In `case "trkall"`, change the per-hash branch so a missing hash cannot serialize as `false`:

```php
			foreach($hash as $h)
			{
				$ret = makeMulticall($cmds,$h,$add,'t',$mode);
				$result[$h] = ($ret === false) ? array() : $ret;
			}
```

- [ ] **Step 3: Validate PHP syntax**

Run:

```bash
php -l overrides/rutorrent/plugins/httprpc/action.php
```

Expected:

```text
No syntax errors detected in overrides/rutorrent/plugins/httprpc/action.php
```

## Task 3: Guard `rutracker_check` State Reads

**Files:**
- Modify: `overrides/rutorrent/plugins/rutracker_check/check.php`

- [ ] **Step 1: Add a shared torrent existence helper**

Add this method near `setState()`:

```php
	static protected function torrentExists( $hash )
	{
		$req = new rXMLRPCRequest( new rXMLRPCCommand( getCmd("d.hash"), $hash ) );
		$req->important = false;
		return($req->run() && !$req->fault);
	}
```

- [ ] **Step 2: Reuse the helper in `setState()`**

Replace the inline `d.hash` check in `setState()` with:

```php
		if(!self::torrentExists($hash))
		{
			self::logDebug("setState: Torrent " . $hash . " not found, skipping state update");
			return(true);
		}
```

- [ ] **Step 3: Guard `getState()` before the `d.custom` multicall**

At the top of `getState()`, before building the `d.get_custom` request, add:

```php
		if(!self::torrentExists($hash))
		{
			$state = self::STE_NOT_NEED;
			$time = time();
			$successful_time = 0;
			$label = "";
			self::logDebug("getState: Torrent " . $hash . " not found, skipping state read");
			return(false);
		}
```

Also mark the custom-field request as non-important:

```php
		$req->important = false;
```

- [ ] **Step 4: Let `run()` exit early for a missing hash**

Change the initial state load in `run()` to:

```php
		if(is_null($state) && !self::getState( $hash, $state, $time, $successful_time, $label ) && ($state == self::STE_NOT_NEED))
			return(true);
```

This keeps the old generic-failure path intact, but stops the plugin from setting `chk-state` on a hash that rTorrent no longer has.

- [ ] **Step 5: Validate PHP syntax**

Run:

```bash
php -l overrides/rutorrent/plugins/rutracker_check/check.php
```

Expected:

```text
No syntax errors detected in overrides/rutorrent/plugins/rutracker_check/check.php
```

## Task 4: Container Verification

**Files:**
- Verify: `Dockerfile`
- Verify: `overrides/rutorrent/js/common.js`
- Verify: `overrides/rutorrent/plugins/httprpc/action.php`
- Verify: `overrides/rutorrent/plugins/rutracker_check/check.php`

- [ ] **Step 1: Build the image**

Run:

```bash
docker build --tag ivanshift/rutorrent:latest .
```

Expected: build exits `0`.

- [ ] **Step 2: Start a fresh container**

Run:

```bash
docker rm -f rutorrent-test 2>/dev/null || true
docker run --name rutorrent-test -d -p 8080:8080 -p 45000:45000 \
  -v rutorrent_config_test:/config -v rutorrent_data_test:/data \
  ivanshift/rutorrent:latest
```

Expected: container starts and stays running.

- [ ] **Step 3: Check PHP runtime inside the container**

Run:

```bash
docker exec rutorrent-test php85 -l /rutorrent/app/plugins/httprpc/action.php
docker exec rutorrent-test php85 -l /rutorrent/app/plugins/rutracker_check/check.php
```

Expected: both commands report no syntax errors.

- [ ] **Step 4: Reproduce stale-hash detail behavior**

With ruTorrent open on the Tracker or Peer tab, replace a torrent through `rutracker_check` so the old hash disappears.

Expected:

- The selected details panel clears when the old hash leaves the torrent list.
- `plugins/httprpc/action.php` returns `[]` for stale `fls`, `prs`, and `trk` requests if a race still slips through.
- `rutracker_check` does not issue `d.custom/chk-state` multicalls for a hash after `d.hash` says it is missing.
- No repeated `invalid parameters: info-hash not found` entries appear for the old hash after the next list refresh.

## Non-Goals

- Do not change the core replacement order in `createTorrent()` until the UI/RPC stale-hash handling is fixed and verified.
- Do not disable XMLRPC fault logging globally; real rTorrent faults should still be visible.
- Do not remove existing `common.js` response guards, because they protect older plugin paths that can still return null or false payloads.

## Acceptance Criteria

- Replacing a torrent with a changed info hash does not leave ruTorrent selected on the removed hash.
- Tracker, peer, and file detail tabs tolerate stale hashes by clearing or returning empty data.
- `rutracker_check` state reads and writes skip hashes that rTorrent no longer knows about.
- PHP and JavaScript syntax checks pass.
- Container build passes.
