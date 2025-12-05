<?php

require_once( 'util.php' );
require_once( 'settings.php' );

class rXMLRPCParam
{
	public $type;
	public $value;

	public function __construct( $aType, $aValue )
	{
		$this->type = $aType;
		if(($this->type=="i8") || ($this->type=="i4"))
			$this->value = number_format($aValue,0,'.','');
		else
			$this->value = htmlspecialchars($aValue,ENT_NOQUOTES,"UTF-8");
	}
}

class rXMLRPCCommand
{
	public $command;
	public $params;

	public function __construct( $cmd, $args = null )
	{
		$this->command = getCmd($cmd);
		$this->params = array();
		rTorrentSettings::get()->patchDeprecatedCommand($this,$cmd);
		if($args!==null) 
		{
		        if(is_array($args))
				foreach($args as $prm)
					$this->addParameter($prm);
			else
				$this->addParameter($args);
		}
	}

	public function addParameters( $args )
	{
		if($args!==null) 
		{
			if(is_array($args))
				foreach($args as $prm)
				$this->addParameter($prm);
			else
				$this->addParameter($args);
		}
	}

	// Several rTorrent command families (d.*, to_*, etc.) assume the first
	// argument is a target string and silently accepted missing targets with
	// xmlrpc-c. Tinyxml2 enforces strict typing, so prepend an empty string
	// when these commands are invoked without an explicit target.
	public function ensureTargetParameter()
	{
		$hasParams = count($this->params) > 0;
		$firstIsEmptyValue = $hasParams && ($this->params[0]->value === '');
		$firstIsString = $hasParams && ($this->params[0]->type === 'string');

		// d.multicall2 requires a leading empty target parameter, even when a view
		// name follows. Ensure it is present.
		if(strpos($this->command, 'd.multicall') === 0)
		{
			if(!$firstIsEmptyValue)
				array_unshift($this->params, new rXMLRPCParam('string', ''));
			return;
		}

		// Global commands still get their first argument treated as a target by
		// tinyxml2. Add an explicit empty target so real parameters are preserved.
		if(!$this->commandNeedsTarget())
		{
			// tinyxml2 always treats the first parameter as the target, even for
			// global commands. Prepend an explicit empty target so the real
			// arguments are not discarded as an "invalid target".
			if(!$hasParams || $firstIsEmptyValue)
				return;
			array_unshift($this->params, new rXMLRPCParam('string', ''));
			return;
		}

		if($firstIsString)
			return;
		array_unshift($this->params, new rXMLRPCParam('string', ''));
	}

	private function commandNeedsTarget()
	{
		static $prefixes = array('d.', 't.', 'p.', 'f.', 'ratio.', 'to_');
		foreach($prefixes as $prefix)
		{
			if(strpos($this->command, $prefix) === 0)
				return true;
		}
		// branch commands operate on a torrent hash even though they lack a scope prefix.
		if(($this->command === 'branch') || ($this->command === 'branch='))
			return true;
		return false;
	}

	public function addParameter( $aValue, $aType = null )
	{
		if($aType===null)
			$aType = self::getPrmType( $aValue );
		$this->params[] = new rXMLRPCParam( $aType, $aValue );
	}

	static protected function getPrmType( $prm )
	{
		if(is_int($prm) && ($prm>=XMLRPC_MIN_I4) && ($prm<=XMLRPC_MAX_I4))
			return('i4');
		if(is_float($prm))
			return('i8');
		return('string');
	}
}

class rXMLRPCRequest
{
	protected $commands = array();
	protected $content = "";
	protected $commandOffset = 0;
	public $i8s = array();
	public $strings = array();
	public $val = array();
	public $fault = false;
	public $parseByTypes = false;
	public $important = true;

	public function __construct( $cmds = null )
	{
		if($cmds)
		{
		        if(is_array($cmds))
				foreach($cmds as $cmd)
					$this->addCommand($cmd);
			else
				$this->addCommand($cmds);
		}
	}

	public static function send( $data, $trusted = true )
	{
		global $rpcLogCalls;
		if($rpcLogCalls)
			FileUtil::toLog($data);
		$result = false;
		$contentlength = strlen($data);
		if($contentlength>0)
		{
			global $rpcTimeOut;
			global $scgi_host;
			global $scgi_port;
			$socket = @fsockopen($scgi_host, $scgi_port, $errno, $errstr, $rpcTimeOut);
			if($socket) 
			{
				$reqheader = "CONTENT_LENGTH\x0".$contentlength."\x0"."CONTENT_TYPE\x0"."text/xml\x0"."SCGI\x0"."1\x0UNTRUSTED_CONNECTION\x0".($trusted ? "0" : "1")."\x0";
				$tosend = strlen($reqheader).":{$reqheader},{$data}";
				@fwrite($socket,$tosend,strlen($tosend));
				$result = '';
				while($data = fread($socket, 4096))
					$result .= $data;
				fclose($socket);
			}
		}
		if($rpcLogCalls)
			FileUtil::toLog($result);
		return($result);
	}

	public function setParseByTypes( $enable = true )
	{
		$this->parseByTypes = $enable;
	}

	public function getCommandsCount()
	{
		return(count($this->commands));
	}

	protected function makeNextCall()
	{
		$this->fault = false;
		$this->content = "";
		$cnt = count($this->commands) - $this->commandOffset;
		if($cnt>0)
		{
			$this->content = '<?xml version="1.0" encoding="UTF-8"?><methodCall><methodName>';
			if($cnt==1)
			{
				$cmd = $this->commands[$this->commandOffset++];
				$cmd->ensureTargetParameter();
	        		$this->content .= "{$cmd->command}</methodName><params>\r\n";
	        		foreach($cmd->params as &$prm)
	        			$this->content .= "<param><value><{$prm->type}>{$prm->value}</{$prm->type}></value></param>\r\n";
		        }
			else
			{
				$maxContentSize = rTorrentSettings::get()->maxContentSize();
				$this->content .= "system.multicall</methodName><params><param><value><array><data>";
				for(; $this->commandOffset < count($this->commands); $this->commandOffset++)
				{
					$cmd = $this->commands[$this->commandOffset];
					$cmd->ensureTargetParameter();
					$cmdStr = "\r\n<value><struct><member><name>methodName</name><value><string>".
						"{$cmd->command}</string></value></member><member><name>params</name><value><array><data>";
					foreach($cmd->params as &$prm)
						$cmdStr .= "\r\n<value><{$prm->type}>{$prm->value}</{$prm->type}></value>";
					$cmdStr .= "\r\n</data></array></value></member></struct></value>";
					if($this->commandOffset > count($this->commands) - $cnt and
						strlen($this->content) + strlen($cmdStr) + 35 + 22 > $maxContentSize)
						break;
					$this->content .= $cmdStr;
				}
				$this->content .= "\r\n</data></array></value></param>";
			}
			$this->content .= "</params></methodCall>";
		}
		return($cnt>0);
	}

	public function addCommand( $cmd )
	{
		$this->commands[] = $cmd;
	}

	public function run($trusted = true)
	{
	        $ret = false;
		$this->i8s = array();
		$this->strings = array();
		$this->val = array();
		rTorrentSettings::get()->patchDeprecatedRequest($this->commands);
		$this->normalizeCustomCommands();
		$this->commandOffset = 0;
		while($this->makeNextCall())
		{
			$answer = self::send($this->content,$trusted);
			if(!empty($answer))
			{
				if($this->parseByTypes)
				{
					if((preg_match_all("|<value><string>(.*)</string></value>|Us",$answer,$strings)!==false) &&
						count($strings)>1 &&
						(preg_match_all("|<value><i.>(.*)</i.></value>|Us",$answer,$this->i8s)!==false) &&
						count($this->i8s)>1)
					{
						foreach($strings[1] as $str) 
						{
							$this->strings[] = html_entity_decode(
								str_replace( array("\\","\""), array("\\\\","\\\""), $str ),
	 							ENT_COMPAT,"UTF-8");
						}
						$this->i8s = $this->i8s[1];
						$ret = true;
					}
				}
				else
				{
					if((preg_match_all("/<value>(<string>|<i.>)(.*)(<\/string>|<\/i.>)<\/value>/Us",$answer,$response)!==false) &&
						count($response)>2)
					{
						foreach($response[2] as $str) 
						{
							$this->val[] = html_entity_decode(
								str_replace( array("\\","\""), array("\\\\","\\\""), $str ),
	 							ENT_COMPAT,"UTF-8");
						}
						$ret = true;
					}
				}
				if($ret)
				{
					if(strstr($answer,"faultCode")!==false)
					{
						$this->fault = true;
						global $rpcLogFaults;
						if($rpcLogFaults && $this->important)
						{
							FileUtil::toLog($this->content);
							FileUtil::toLog($answer);
						}
						break;
					}
				} else break;
			} else break;
		}
		$this->content = "";
		$this->commands = array();
		return($ret);
	}

	public function success($trusted = true)
	{
		return($this->run($trusted) && !$this->fault);
	}

	// Translate legacy ruTorrent pseudo-methods (trk, stg) into real rTorrent calls.
	protected function normalizeCustomCommands()
	{
		$normalized = array();
		foreach($this->commands as $cmd)
		{
			if($cmd->command === 'trk')
			{
				$hash = (count($cmd->params) > 0) ? $cmd->params[0]->value : '';
				$fields = array(
					getCmd("t.get_url="), getCmd("t.get_type="), getCmd("t.is_enabled="), getCmd("t.get_group="),
					getCmd("t.get_scrape_complete="), getCmd("t.get_scrape_incomplete="), getCmd("t.get_scrape_downloaded="),
					getCmd("t.get_normal_interval="), getCmd("t.get_scrape_time_last=")
				);
				$cmd->command = getCmd("t.multicall");
				$cmd->params = array(
					new rXMLRPCParam('string', $hash),
					new rXMLRPCParam('string', '')
				);
				foreach($fields as $field)
					$cmd->addParameter($field, 'string');
				$normalized[] = $cmd;
				continue;
			}
			if($cmd->command === 'stg')
			{
				$cmds = array(
					"get_check_hash", "get_bind", "get_dht_port", "get_directory", "get_download_rate",
					"get_hash_interval", "get_hash_max_tries", "get_hash_read_ahead", "get_http_cacert", "get_http_capath",
					"get_http_proxy", "get_ip", "get_max_downloads_div", "get_max_downloads_global", "get_max_file_size",
					"get_max_memory_usage", "get_max_open_files", "get_max_open_http", "get_max_peers", "get_max_peers_seed",
					"get_max_uploads", "get_max_uploads_global", "get_min_peers_seed", "get_min_peers", "get_peer_exchange",
					"get_port_open", "get_upload_rate", "get_port_random", "get_port_range", "get_preload_min_size",
					"get_preload_required_rate", "get_preload_type", "get_proxy_address", "get_receive_buffer_size", "get_safe_sync",
					"get_scgi_dont_route", "get_send_buffer_size", "get_session", "get_session_lock", "get_session_on_completion",
					"get_split_file_size", "get_split_suffix", "get_timeout_safe_sync", "get_timeout_sync", "get_tracker_numwant",
					"get_use_udp_trackers", "get_max_uploads_div", "get_max_open_sockets"
				);
				if(rTorrentSettings::get()->iVersion>=0x900)
					$cmds[5] = $cmds[6] = $cmds[7] = "cat";
				$normalized[] = new rXMLRPCCommand("dht_statistics");
				foreach($cmds as $subCmd)
					$normalized[] = new rXMLRPCCommand(getCmd($subCmd));
				continue;
			}
			$normalized[] = $cmd;
		}
		$this->commands = $normalized;
	}
}

function getCmd($cmd)
{
	return(rTorrentSettings::get()->getCommand($cmd));
}
