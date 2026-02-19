<?php

require_once( "../../php/xmlrpc.php" );
require_once( "../../php/Torrent.php" );
require_once( "../../php/rtorrent.php" );
require_once( './util_rt.php' );

//------------------------------------------------------------------------------
// Move torrent data of $hash torrent to new location at $dest_path
//------------------------------------------------------------------------------
function rtSetDataDir( $hash, $dest_path, $add_path, $move_files, $fast_resume, $dbg = false )
{
	if( $dbg ) rtDbg( __FUNCTION__, "hash        : ".$hash );
	if( $dbg ) rtDbg( __FUNCTION__, "dest_path   : ".$dest_path );
	if( $dbg ) rtDbg( __FUNCTION__, "add path    : ".($add_path ? "1" : "0") );
	if( $dbg ) rtDbg( __FUNCTION__, "move files  : ".($move_files ? "1" : "0") );
	if( $dbg ) rtDbg( __FUNCTION__, "fast resume : ".($fast_resume ? "1" : "0") );

	$is_open       = false;
	$is_active     = false;
	$is_multy_file = false;
	$base_name     = '';
	$base_path     = '';
	$base_file     = '';

	$is_ok = true;
	if( $dest_path == '' )
	{
		$is_ok = false;
	}
	else {
		$dest_path = rtAddTailSlash( $dest_path );
	}

	// Check if torrent is open or active
	if( $is_ok )
	{
		$req = rtExec( array( getCmd("d.is_open"), getCmd("d.is_active") ), $hash, $dbg );
		if( !$req )
			$is_ok = false;
		else {
			$is_open   = ( $req->val[0] != 0 );
			$is_active = ( $req->val[1] != 0 );
			if( $dbg ) rtDbg( __FUNCTION__, "is_open=".$req->val[0].", is_active=".$req->val[1] );
		}
	}

	// Open closed torrent to get d.get_base_path, d.get_base_filename
		if( $is_ok && $move_files )
		{
			if( !$is_open && !rtExec( getCmd("d.open"), $hash, $dbg ) )
			{
				$is_ok = false;
			}
		}

	// Ask info from rTorrent
	if( $is_ok && $move_files )
	{
		$req = rtExec(
			array( 	getCmd("d.get_name"), 
				getCmd("d.get_base_path"), 
				getCmd("d.get_base_filename"), 
				getCmd("d.is_multi_file"), 
				getCmd("d.get_complete") ),
			$hash, $dbg );
		if( !$req )
			$is_ok = false;
		else {
			$base_name     = trim( $req->val[0] );
			$base_path     = trim( $req->val[1] );
			$base_file     = trim( $req->val[2] );
			$is_multy_file = ( $req->val[3] != 0 );
			if( $req->val[4] == 0 ) // if torrent is not completed -> "fast start" is impossible
				$fast_resume = false;
			if( $dbg ) rtDbg( __FUNCTION__, "d.get_name          : ".$base_name );
			if( $dbg ) rtDbg( __FUNCTION__, "d.get_base_path     : ".$base_path );
			if( $dbg ) rtDbg( __FUNCTION__, "d.get_base_filename : ".$base_file );
			if( $dbg ) rtDbg( __FUNCTION__, "d.is_multy_file     : ".$req->val[3] );
			if( $dbg ) rtDbg( __FUNCTION__, "d.get_complete      : ".$req->val[4] );
		}
	}

	// Check if paths are valid
	if( $is_ok && $move_files )
	{
		if( $base_path == '' || $base_file == '' )
		{
			if( $dbg ) rtDbg( __FUNCTION__, "base paths are empty" );
			$is_ok = false;
		}
		else {
			// Make $base_path a really BASE path for downloading data
			// (not including single file or subdir for multiple files).
			// Add trailing slash, if none.
			$base_path = rtRemoveTailSlash( $base_path );
			$base_path = rtRemoveLastToken( $base_path, '/' );	// filename or dirname
			$base_path = rtAddTailSlash( $base_path );
		}
	}

	// Get list of torrent data files
	$torrent_files = array();
	if( $is_ok && $move_files )
	{
		// Version-aware multicall to support rTorrent 0.9.4+ (f.multicall2) and older builds.
		$req = new rXMLRPCRequest(
			new rXMLRPCCommand( getCmd("f.multicall"), array( $hash, "", getCmd("f.get_path=") ) )
		);
		// File list polling can race with torrent erase and return "info-hash not found".
		$req->important = false;
		if( !$req->run() || $req->fault )
		{
			if( $dbg ) rtDbg( __FUNCTION__, "f.multicall failed" );
			$is_ok = false;
		}
		else {
			$torrent_files = $req->val;
			if( $dbg ) rtDbg( __FUNCTION__, "files in torrent    : ".count( $torrent_files ) );
		}
	}

	// 1. Stop torrent if active (if not, then rTorrent can crash)
	// 2. Close torrent anyway
	if( $is_ok )
	{
		$cmds = array();
		if( $is_active ) $cmds[] = getCmd("d.stop");
		if( $is_open || $move_files ) $cmds[] = getCmd("d.close");
		if( count( $cmds ) > 0 && !rtExec( $cmds, $hash, $dbg ) )
			$is_ok = false;
	}

	// Move torrent data files to new location
	if( $is_ok && $move_files )
	{
		$full_base_path = $base_path;
		$full_dest_path = $dest_path;
		// Don't use "count( $torrent_files ) > 1" check (there can be one file in a subdir)
		if( $is_multy_file )
		{
			// torrent is a directory
			$full_base_path .= rtAddTailSlash( $base_file );	
			$full_dest_path .= $add_path ? rtAddTailSlash( $base_name ) : "";
		}
		else {
			// torrent is a single file
		}

		if( $dbg ) rtDbg( __FUNCTION__, "from ".$full_base_path );
		if( $dbg ) rtDbg( __FUNCTION__, "to   ".$full_dest_path );
		
		if( $full_base_path != $full_dest_path && is_dir( $full_base_path ) )
		{
			if( !rtOpFiles( $torrent_files, $full_base_path, $full_dest_path, "Move", $dbg ) )
				$is_ok = false;
			else {
				// Recursively remove source dirs without files
				if( $dbg ) rtDbg( __FUNCTION__, "clean ".$full_base_path );
				if( $is_multy_file )
				{
					rtRemoveDirectory( $full_base_path, false );
					if( $dbg && is_dir( $full_base_path ) )
						rtDbg( __FUNCTION__, "some files were not deleted" );
				}
			}
		}
	}

	if( $is_ok )
	{
		// fast resume is requested
		if( $fast_resume )
		{
			if( $dbg ) rtDbg( __FUNCTION__, "trying fast resume" );
			// collect variables
			$session      = rTorrentSettings::get()->session;
			$tied_to_file = null;
			$label        = null;
			$addition     = null;
			$req = rtExec( array( 
					getCmd("get_session"), 
					getCmd("d.get_tied_to_file"),
					getCmd("d.get_custom1"),
					getCmd("d.get_connection_seed"),
					getCmd("d.get_throttle_name"),
					), 
					$hash, $dbg );
			if( !$req )
			{
				$fast_resume = false;
			}
			else {
				$session      = $req->val[0];
				$tied_to_file = $req->val[1];
				$label        = rawurldecode( $req->val[2] );
				$addition     = array(); 
				if( !empty( $req->val[3] ) )
					$addition[] = getCmd( "d.set_connection_seed=" ).$req->val[3];
				if( !empty( $req->val[4] ) )
					$addition[] = getCmd( "d.set_throttle_name=" ).$req->val[4];
				// build path to .torrent file
				$fname = rtAddTailSlash( $session ).$hash.".torrent";
				if( empty( $session ) || !is_readable( $fname ) )
				{
					if( !strlen( $tied_to_file ) || !is_readable( $tied_to_file ) )
					{
						if( $dbg ) rtDbg( __FUNCTION__, "empty session or inaccessible .torrent file" );
						$fast_resume = false;
					}
					else
						$fname = $tied_to_file;
				}
				if( $fast_resume )
					$fast_resume = rtFastResume( $fname, $dest_path, $add_path, $label, $addition, $dbg );
			}
		}

		if( $fast_resume )
			return( rtFastResumeResult( $hash, $dbg ) );
		
		// Set new directory for torrent
		if( $is_ok )
		{
			$req = new rXMLRPCRequest( new rXMLRPCCommand( $add_path ? getCmd("d.set_directory") : getCmd("d.set_directory_base"), array($hash, $dest_path) ) );
			if( !$req->success() )
				$is_ok = false;
		}
	}

	// Reopen torrent, if needed
	if( $is_ok )
	{
			$req = rtExec( array(), $hash, $dbg );
			if( !$req )
				$is_ok = false;
			else {
				if( !$is_open )
					$req->addCommand( new rXMLRPCCommand( getCmd("d.close"), $hash ) );
				if( $is_active )
					$req->addCommand( new rXMLRPCCommand( getCmd("d.start"), $hash ) );
				if( !$req->success() )
					$is_ok = false;
			}
		}

	return( $is_ok );
}
