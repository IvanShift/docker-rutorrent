<?php

require_once( '../../php/xmlrpc.php' );
require_once( 'rpccache.php' );

$mode = "raw";
$add = array();
$ss = array();
$vs = array();
$hash = array();
if (!isset($HTTP_RAW_POST_DATA))
	$HTTP_RAW_POST_DATA = file_get_contents("php://input");
if(isset($HTTP_RAW_POST_DATA))
{
	$vars = explode('&', $HTTP_RAW_POST_DATA);
	foreach($vars as $var)
	{
		$parts = explode("=",$var);
		switch($parts[0])
		{
			case "cmd":
			{
				$c = getCmd(rawurldecode($parts[1]));
				if(strpos($c,"execute")===false)
					$add[] = $c;
				break;  	
			}
			case "s":
			{
				$ss[] = rawurldecode($parts[1]);
				break;
			}
			case "v":
			{
				$vs[] = rawurldecode($parts[1]);
				break;
			}
			case "hash":
			{
				$hash[] = $parts[1];
				break;
			}
			case "mode":
			{
				$mode  = $parts[1];
				break;
			}
			case "cid":
			{
				$cid  = $parts[1];
				break;
			}
		}
	}
}

function makeMulticall($cmds,$hash,$add,$prefix)
{
	// Version-aware multicall keeps compatibility with rtorrent 0.9.4+ (multicall2) and older releases.
	$cmd = new rXMLRPCCommand( getCmd($prefix.".multicall"), array( $hash, "" ) );
	$cmd->addParameters( array_map("getCmd", $cmds) );
	foreach( $add as $prm )
		$cmd->addParameter($prm);
	$cnt = count($cmds)+count($add);
	$req = new rXMLRPCRequest($cmd);
	if($req->success(false))
	{
	        $result = array();
		for($i = 0; $i<count($req->val); $i+=$cnt)
			$result[] = array_slice($req->val, $i, $cnt);
		return($result);
	}
	return(false);
}

function makeSimpleCall($cmds,$hash)
{
	$req = new rXMLRPCRequest();
	foreach($hash as $h)
		foreach($cmds as $cmd)	
			$req->addCommand( new rXMLRPCCommand( $cmd, $h ) );
       	return($req->success(false) ? $req->val : false);
}

$result = null;

switch($mode)
{
	case "list":	/**/
	{
		$cmds = array(
			"d.get_hash=", "d.is_open=", "d.is_hash_checking=", "d.is_hash_checked=", "d.get_state=",
			"d.get_name=", "d.get_size_bytes=", "d.get_completed_chunks=", "d.get_size_chunks=", "d.get_bytes_done=",
			"d.get_up_total=", "d.get_ratio=", "d.get_up_rate=", "d.get_down_rate=", "d.get_chunk_size=",
			"d.get_custom1=", "d.get_peers_accounted=", "d.get_peers_not_connected=", "d.get_peers_connected=", "d.get_peers_complete=",
			"d.get_left_bytes=", "d.get_priority=", "d.get_state_changed=", "d.get_skip_total=", "d.get_hashing=",
			"d.get_chunks_hashed=", "d.get_base_path=", "d.get_creation_date=", "d.get_tracker_size=", "d.is_active=",
			"d.get_message=", "d.get_custom2=", "d.get_free_diskspace=", "d.is_private=", "d.is_multi_file="
			);
		$cmd = new rXMLRPCCommand( getCmd("d.multicall"), "main" );
		$cmd->addParameters( array_map("getCmd", $cmds) );
		foreach( $add as $prm )
			$cmd->addParameter($prm);
		$cnt = count($cmds)+count($add);
		$req = new rXMLRPCRequest($cmd);
		if($req->success(false))
		{
			$theCache = new rpcCache();
			$dTorrents = array();
			$torrents = array();
			foreach($req->val as $index=>$value) 
			{
				if($index % $cnt == 0) 
				{
					$current_index = $value;
					$torrents[$current_index] = array();
				} 
				else
					$torrents[$current_index][] = $value;
			}

			$theCache->calcDifference( $cid, $torrents, $dTorrents );
			$result = array( "t"=>$torrents, "cid"=>$cid );
			if(count($dTorrents))
				$result["d"] = $dTorrents;
		}
		break;
	}
	case "fls":	/**/
	{
		$result = makeMulticall(array(
			"f.get_path=", "f.get_completed_chunks=", "f.get_size_chunks=", "f.get_size_bytes=", "f.get_priority="
			),$hash[0],$add,'f');
		// Graceful handling when torrent was deleted (info-hash not found)
		if($result === false)
			$result = array();
		break;
	}
	case "prs":	/**/
	{
		$result = makeMulticall(array(
			"p.get_id=", "p.get_address=", "p.get_client_version=", "p.is_incoming=", "p.is_encrypted=",
			"p.is_snubbed=", "p.get_completed_percent=", "p.get_down_total=", "p.get_up_total=", "p.get_down_rate=",
			"p.get_up_rate=", "p.get_id_html=", "p.get_peer_rate=", "p.get_peer_total=", "p.get_port="
			),$hash[0],$add,'p');
		// Graceful handling when torrent was deleted (info-hash not found)
		if($result === false)
			$result = array();
		break;
	}
	case "trk":	/**/
	{
		$result = makeMulticall(array(
		        "t.get_url=", "t.get_type=", "t.is_enabled=", "t.get_group=", "t.get_scrape_complete=",
			"t.get_scrape_incomplete=", "t.get_scrape_downloaded=",
			"t.get_normal_interval=", "t.get_scrape_time_last="
			),$hash[0],$add,'t');
		break;
	}
	case "stg":	/**/
	{
		$dhtPortGetter = (rTorrentSettings::get()->iVersion >= 0x1000) ? "dht.port" : "get_dht_port";
		// rTorrent 0.16.x (0.10.2+): throttle.*.div and throttle.*.global need ._val suffix to read values
		$isRt16 = (rTorrentSettings::get()->iVersion >= 0x1002);
		$maxDownloadsDiv    = $isRt16 ? "throttle.max_downloads.div._val" : "get_max_downloads_div";
		$maxDownloadsGlobal = $isRt16 ? "throttle.max_downloads.global._val" : "get_max_downloads_global";
		$maxUploadsDiv      = $isRt16 ? "throttle.max_uploads.div._val" : "get_max_uploads_div";
		$maxUploadsGlobal   = $isRt16 ? "throttle.max_uploads.global._val" : "get_max_uploads_global";
		$cmds = array(
			"get_check_hash", "get_bind", $dhtPortGetter, "get_directory", "get_download_rate",
			"get_hash_interval", "get_hash_max_tries", "get_hash_read_ahead", "get_http_cacert", "get_http_capath",
			"get_http_proxy", "get_ip", $maxDownloadsDiv, $maxDownloadsGlobal, "get_max_file_size",
			"get_max_memory_usage", "get_max_open_files", "get_max_open_http", "get_max_peers", "get_max_peers_seed",
			"get_max_uploads", $maxUploadsGlobal, "get_min_peers_seed", "get_min_peers", "get_peer_exchange",
			"get_port_open", "get_upload_rate", "get_port_random", "get_port_range", "get_preload_min_size",
			"get_preload_required_rate", "get_preload_type", "get_proxy_address", "get_receive_buffer_size", "get_safe_sync",
			"get_scgi_dont_route", "get_send_buffer_size", "get_session", "get_session_lock", "get_session_on_completion",
			"get_split_file_size", "get_split_suffix", "get_timeout_safe_sync", "get_timeout_sync", "get_tracker_numwant",
			"get_use_udp_trackers", $maxUploadsDiv, "get_max_open_sockets"
			);
		if(rTorrentSettings::get()->iVersion>=0x900)
			$cmds[5] = $cmds[6] = $cmds[7] = "cat";
		$req = new rXMLRPCRequest( new rXMLRPCCommand( "dht_statistics" ) );
		foreach( $cmds as $cmd )
			$req->addCommand( new rXMLRPCCommand( getCmd($cmd) ) );
		foreach( $add as $prm )
			$req->addCommand( new rXMLRPCCommand( $prm ) );
		if($req->success(false))
		{
			$expectedSettings = count($cmds);
			$dhtValuesCount = max(0, count($req->val) - $expectedSettings - count($add));
			$dhtValues = array_slice($req->val, 0, $dhtValuesCount);
			$settingsValues = array_slice($req->val, $dhtValuesCount, $expectedSettings);
			$dhtMode = (count($dhtValues) > 5 && ($dhtValues[0] != '0')) ? $dhtValues[5] :
				((count($dhtValues) > 1) ? $dhtValues[1] : '');
			for($i = 0; $i < $expectedSettings; $i++)
			{
				if(($cmds[$i]=="cat") && (!array_key_exists($i, $settingsValues) || ($settingsValues[$i]=="")) && count($dhtValues))
					$settingsValues[$i] = $dhtValues[0];
			}
			$result = array_merge(
				array((($dhtMode=="auto") || ($dhtMode=="on")) ? 1 : 0),
				$settingsValues
			);
			if(count($result) < 1 + $expectedSettings)
				$result = array_pad($result, 1 + $expectedSettings, "");
		}
		break;
	}

	case "ttl":	/**/
	{
		$cmds = array(
		        "get_up_total", "get_down_total", "get_upload_rate", "get_download_rate"
		        );
		$req = new rXMLRPCRequest();
		foreach( $cmds as $cmd )
			$req->addCommand( new rXMLRPCCommand( $cmd ) );	
		foreach( $add as $prm )
			$req->addCommand( new rXMLRPCCommand( $prm ) );	
		$expected = count($cmds) + count($add);
		if($req->success(false))
	        	$result = $req->val;
		// When the totals request fails (e.g. rTorrent offline), send zeroed values
		// so the UI can continue updating without throwing errors on null responses.
		if(!is_array($result))
			$result = array_fill(0, $expected, 0);
		else if(count($result) < $expected)
			$result = array_pad($result, $expected, 0);
		break;
	}
	case "opn":	/**/
	{
		$cmds = array(
			"network.http.current_open", "network.open_sockets"
		);
		if (rTorrentSettings::get()->apiVersion >= 11)
			$cmds[] = "network.open_files";
		$req = new rXMLRPCRequest();
		foreach( $cmds as $cmd )
			$req->addCommand( new rXMLRPCCommand( $cmd ) );
		if($req->success(false)) {
			$result = $req->val;
			if (count($cmds) < 3)
				$result[] = -1;
		}
		break;
	}
	case "prp":	/**/
	{
		$cmds = array(
			"d.get_peer_exchange", "d.get_peers_max", "d.get_peers_min", "d.get_tracker_numwant", "d.get_uploads_max",
			"d.is_private", "d.get_connection_seed"
		        );
		$req = new rXMLRPCRequest();
		foreach( $cmds as $cmd )
			$req->addCommand( new rXMLRPCCommand( $cmd, $hash[0] ) );	
		foreach( $add as $prm )
			$req->addCommand( new rXMLRPCCommand( $prm, $hash[0] ) );	
		if($req->success(false))
	        	$result = $req->val;
		break;
	}
	case "trkstate":	/**/
	{
		$req = new rXMLRPCRequest();
		foreach($vs as $ndx=>$value)
			$req->addCommand( new rXMLRPCCommand( "t.set_enabled", array($hash[0], intval($value), intval($ss[0])) ) );
		if($req->success(false))
	        	$result = $req->val;
		break;
	}
	case "setprio":	/**/
	{
		$req = new rXMLRPCRequest();
		foreach($vs as $v)
			$req->addCommand( new rXMLRPCCommand( "f.set_priority", array($hash[0], intval($v), intval($ss[0])) ) );
		$req->addCommand( new rXMLRPCCommand("d.update_priorities", $hash[0]) );
		if($req->success(false))
	        	$result = $req->val;
		break;
	}
	case "setsettings":
	{
		$req = new rXMLRPCRequest();
		foreach($vs as $ndx=>$v)
		{
			if($ss[$ndx][0]=='n')
				$v = floatval($v);
			if(($ss[$ndx]=="sdirectory") && !rTorrentSettings::get()->correctDirectory($v))
				continue;
			$settingName = substr($ss[$ndx], 1);
			// rTorrent 0.9.x+/0.16.x dropped the hash_* setters; skip them to avoid -506 faults.
			if (rTorrentSettings::get()->iVersion >= 0x900 &&
			    in_array($settingName, array("hash_interval", "hash_max_tries", "hash_read_ahead"), true)) {
				continue;
			}
			if($ss[$ndx]=="ndht")
				$cmd = new rXMLRPCCommand('dht', (($v==0) ? "disable" : "auto"));
			else if($settingName === 'dht_port' && rTorrentSettings::get()->iVersion >= 0x1000)
			{
				// For rTorrent 0.16+, we need to restart DHT to apply the new port.
				// Sequence: disable DHT -> set override port -> re-enable DHT
				$req->addCommand(new rXMLRPCCommand('dht', 'disable'));
				$req->addCommand(new rXMLRPCCommand('dht.override_port.set', $v));
				$cmd = new rXMLRPCCommand('dht', 'auto');
			}
			else
				$cmd = new rXMLRPCCommand('set_'.$settingName, $v);
			$req->addCommand($cmd);
		}
		if($req->getCommandsCount())
		{
			if($req->success(false))
		        	$result = $req->val;
        	}
        	else
	        	$result = array();
		break;
	}
	case "recheck":	/**/
	{
        	$result = makeSimpleCall(array("d.check_hash"), $hash);
		break;
	}
	case "pause":	/**/
	{
        	$result = makeSimpleCall(array("d.stop"), $hash);
		break;
	}
	case "unpause":	/**/
	{
        	$result = makeSimpleCall(array("d.start"), $hash);
		break;
	}
	case "start":	/**/
	{
        	$result = makeSimpleCall(array("d.start"), $hash);
		break;
	}
	case "stop":	/**/
	{
        	$result = makeSimpleCall(array("d.stop"), $hash);
		break;
	}
	case "close":	/**/
	{
        	$result = makeSimpleCall(array("d.close"), $hash);
		break;
	}
	case "dsetprio":	/**/
	{
		$req = new rXMLRPCRequest();
		foreach($hash as $h)
			$req->addCommand( new rXMLRPCCommand( "d.set_priority", array($h, intval($vs[0])) ) );
		if($req->success(false))
	        	$result = $req->val;
		break;
	}
	case "setlabel":	/**/
	{
		$req = new rXMLRPCRequest();
		foreach($hash as $h)
			$req->addCommand( new rXMLRPCCommand( "d.set_custom1", array($h, $vs[0]) ) );
		if($req->success(false))
	        	$result = $req->val;
		break;
	}
	case "setprops":	/**/
	{
		$req = new rXMLRPCRequest();
		foreach($ss as $ndx=>$s)
		{
			if($s=="superseed")
			{
        			$conn = ($vs[$ndx]!=0) ? "initial_seed" : "seed";
				$cmd = new rXMLRPCCommand("branch", array(
					$hash[0],
					getCmd("d.is_active="),
					getCmd("cat").'=$'.getCmd("d.stop=").',$'.getCmd("d.close=").',$'.getCmd("d.set_connection_seed=").$conn.',$'.getCmd("d.open=").',$'.getCmd("d.start="),
					getCmd("d.set_connection_seed=").$conn
					));
			}
			else
			{
				if($s=="ulslots")
					$cmd = new rXMLRPCCommand("d.set_uploads_max");
				else
				if($s=="pex")
					$cmd = new rXMLRPCCommand("d.set_peer_exchange");
				else
					$cmd = new rXMLRPCCommand("d.set_".$s);
				$cmd->addParameters( array($hash[0], $vs[$ndx]) );
			}
			$req->addCommand($cmd);
		}
		if($req->success(false))
	        	$result = $req->val;
		break;
	}
	case "setul":	/**/
	{
		$req = new rXMLRPCRequest( new rXMLRPCCommand("set_upload_rate", $ss[0]) );
		if($req->success(false))
	        	$result = $req->val;
		break;
	}
	case "setdl":	/**/
	{
		$req = new rXMLRPCRequest( new rXMLRPCCommand("set_download_rate", $ss[0]) );
		if($req->success(false))
	        	$result = $req->val;
		break;
	}
	case "unsnub":
	case "snub":
	{
		$on = (($mode=="snub") ? 1 : 0);
		$req = new rXMLRPCRequest();
                foreach($vs as $v)
			$req->addCommand( new rXMLRPCCommand("p.snubbed.set", array($hash[0].":p".$v,$on)) );
		if($req->success(false))
	        	$result = $req->val;
		break;
	}
	case "ban":
	{
		$req = new rXMLRPCRequest();
                foreach($vs as $v)
		{
			$req->addCommand( new rXMLRPCCommand("p.banned.set", array($hash[0].":p".$v,1)) );
			$req->addCommand( new rXMLRPCCommand("p.disconnect", $hash[0].":p".$v) );
		}
		if($req->success(false))
	        	$result = $req->val;
		break;
	}
	case "kick":
	{
		$req = new rXMLRPCRequest();
                foreach($vs as $v)
			$req->addCommand( new rXMLRPCCommand("p.disconnect", $hash[0].":p".$v) );
		if($req->success(false))
	        	$result = $req->val;
		break;
	}
	case "add_peer":
	{
		$req = new rXMLRPCRequest(
			new rXMLRPCCommand( "add_peer", array($hash[0], $vs[0]) ) );
		if($req->success(false))
	        	$result = $req->val;
		break;
	}
	case "remove":	/**/
	{
		// Mirror ruTorrent "remove" action (erase torrent only)
		$result = makeSimpleCall(array("d.erase"), $hash);
		break;
	}
	case "removewithdata":	/**/
	{
		// Match erasedata plugin: mark deletion mode, drop tied file, then erase torrent
		$deleteMode = count($vs) ? $vs[0] : 1;
		$req = new rXMLRPCRequest();
		foreach($hash as $h)
		{
			$req->addCommand( new rXMLRPCCommand( "d.set_custom5", array($h, $deleteMode) ) );
			$req->addCommand( new rXMLRPCCommand( "d.delete_tied", $h ) );
			$req->addCommand( new rXMLRPCCommand( "d.erase", $h ) );
		}
		if($req->success(false))
			$result = $req->val;
		break;
	}
	case "erase":	/**/
	{
        	$result = makeSimpleCall(array("d.erase"), $hash);
		break;
	}
	case "get":	/**/
	{
		$cmds = array();
		foreach($vs as $cmd)
			$cmds[] = getCmd(rawurldecode($cmd));
		$result = makeMulticall($cmds,$hash[0],$add,'d');
		break;
	}
	case "trkall":	/**/
	{
		$cmds = array(
		        "t.get_url=", "t.get_type=", "t.is_enabled=", "t.get_group=", "t.get_scrape_complete=", 
			"t.get_scrape_incomplete=", "t.get_scrape_downloaded="
		        );
		$result = array();
		if(empty($hash))
		{
			$prm = getCmd("cat").'="$'.getCmd("t.multicall=").getCmd("d.get_hash=").",";
			foreach( $cmds as $tcmd )
				$prm.=getCmd($tcmd).','.getCmd("cat=#").',';
			foreach( $add as $tcmd )
				$prm.=getCmd($tcmd).','.getCmd("cat=#").',';
			$prm = substr($prm, 0, -1).'"';
			$cnt = count($cmds)+count($add);
			$req = new rXMLRPCRequest();
			// Version-aware multicall for tracker list aggregation
			$req->addCommand( new rXMLRPCCommand( getCmd("d.multicall"), array
			( 
				"main",
				getCmd("d.get_hash="),
				$prm
			) ) );						
       		if($req->success(false))
			{
				for( $i = 0; $i< count($req->val); $i+=2 )
				{
					$tracker = explode( '#', $req->val[$i+1] );
					if(!empty($tracker))
						unset( $tracker[ count($tracker)-1 ] );
					$result[ $req->val[$i] ] = array_chunk( $tracker, $cnt );
				}
			}									
		}
		else
		{
			foreach($hash as $ndx=>$h)
			{
				$result[$h] = makeMulticall($cmds,$h,$add,'t');
			}
		}
		break;
	}
	case "getchunks":	/**/
	{
		$req = new rXMLRPCRequest( array(
			new rXMLRPCCommand( getCmd("d.get_bitfield"), $hash[0] ),
			new rXMLRPCCommand( getCmd("d.get_chunk_size"), $hash[0] ),
			new rXMLRPCCommand( getCmd("d.get_size_chunks"), $hash[0] )
		) );
		if(rTorrentSettings::get()->apiVersion>=4)
			$req->addCommand(new rXMLRPCCommand( getCmd("d.chunks_seen"), $hash[0] ));
		if($req->success(false))
		{
			$result = array(
				"chunks"=>$req->val[0],
				"size"=>$req->val[1],
				"tsize"=>$req->val[2]
			);
			if(rTorrentSettings::get()->apiVersion>=4)
				$result["seen"] = $req->val[3];
		}
		break;
	}
	default:
	{
		// Fallback for raw XML payloads (actions not covered by httprpc).
		if(isset($HTTP_RAW_POST_DATA) && stripos($HTTP_RAW_POST_DATA, '<methodCall') !== false)
		{
			$raw = rXMLRPCRequest::send($HTTP_RAW_POST_DATA, false);
			if(!empty($raw))
			{
				$pos = strpos($raw, "\r\n\r\n");
				if($pos !== false)
					$raw = substr($raw, $pos + 4);
				CachedEcho::send($raw, "text/xml");
				return;
			}
		}

		$req = new rXMLRPCRequest();
		if(count($hash)==0)
			$req->addCommand( new rXMLRPCCommand( $mode ) );
		else
			foreach($hash as $h)
				$req->addCommand( new rXMLRPCCommand( $mode, $h ) );
		foreach( $add as $prm )
			if(is_array($prm))
				$req->addCommand( new rXMLRPCCommand( $prm[0], array_merge(array($hash[0]),$prm[1]) ) );	
			else
				$req->addCommand( new rXMLRPCCommand( $prm, $hash[0] ) );	
		if($req->success(false))
	        	$result = $req->val;
		break;
	}
}

CachedEcho::send(JSON::safeEncode($result),"application/json");
