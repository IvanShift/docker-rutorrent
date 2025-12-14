<?php
require_once( 'util.php' );

class rCache
{
	protected $dir;

	public function __construct( $name = '' )
	{
		$this->dir = FileUtil::getSettingsPath().$name;
		if(!is_dir($this->dir))
			FileUtil::makeDirectory($this->dir);
	}
	public static function flock( $fp )
	{
		$i = 0;
		while(!flock($fp, LOCK_EX | LOCK_NB))
		{
			usleep(round(rand(0, 100)*1000));
			if(++$i>20)
				return(false);
		}
		return(true);
	}
	public function set( $rss, $arg = null )
	{
		global $profileMask;
		$name = $this->getName($rss);
		if(     is_object($rss) &&
			isset($rss->modified) &&
			method_exists($rss,"merge") &&
			($rss->modified < filemtime($name)))
		{
		        $className = get_class($rss);
			$newInstance = new $className();
			if($this->get($newInstance) &&
				!$rss->merge($newInstance, $arg))
				return(false);
		}
		$fp = fopen( $name.'.tmp', "a" );
		if($fp!==false)
		{
			if(self::flock( $fp ))
			{
				ftruncate( $fp, 0 );
				$str = serialize( $rss );
	        		if((fwrite( $fp, $str ) == strlen($str)) && fflush( $fp ))
	        		{
					flock( $fp, LOCK_UN );
        				if(fclose( $fp ) !== false)
        				{
	       					@rename( $name.'.tmp', $name );
						@chmod($name,$profileMask & 0666);
	        				return(true);
					}
					else
						unlink( $name.'.tmp' );
				}
				else
				{
					flock( $fp, LOCK_UN );
        				fclose( $fp );
        				unlink( $name.'.tmp' );
				}	        			
	        	}
	        	else
		        	fclose( $fp );
		}
	        return(false);
	}
	public function get( &$rss )
	{
		$fname = $this->getName($rss);
		$data = @file_get_contents($fname);
		if($data===false)
			return(false);

		$mtime = @filemtime($fname);
		$hadWarning = false;
		// Capture unserialize warnings (e.g., trailing garbage) so we can heal the cache.
		$tmp = $this->safeUnserialize($data, $hadWarning);
		$isSerializedFalse = ($tmp === false && trim($data) === 'b:0;');

		$ret = false;
		if(is_array($tmp))
		{
			$rss = $tmp;				
			$ret = true;
		}
		else
		{
			if(($tmp!==false || $isSerializedFalse) && 
				(!isset($rss->version) || 
				(isset($rss->version) && !isset($tmp->version)) ||
				(isset($tmp->version) && ($tmp->version==$rss->version))))
			{
				$rss = $tmp;
				if(is_object($rss))
					$rss->modified = $mtime;
				$ret = true;
			}
		}

		if($ret)
		{
			// Heal corrupted cache files (extra trailing data) so future reads stay quiet.
			if($hadWarning)
			{
				$this->set($rss);
				if($mtime !== false)
					@touch($this->getName($rss), $mtime);
			}
		}
		elseif($hadWarning)
			$this->remove($rss);

		return($ret);
	}
	public function remove( $rss )
	{
		return(@unlink($this->getName($rss)));
	}
	protected function getName($rss)
	{
	        return($this->dir."/".(is_object($rss) ? $rss->hash : $rss['__hash__']));
	}
	public function getModified( $obj = null )
	{
		return(filemtime( is_null($obj) ? $this->dir : 
			(is_object($obj) ? $this->getName($obj) : $this->dir."/".$obj) ));
			
	}

	protected function safeUnserialize($data, &$hadWarning)
	{
		$hadWarning = false;
		set_error_handler(function($severity, $message) use (&$hadWarning) {
			if($severity === E_WARNING && strpos($message, 'unserialize()') !== false)
			{
				$hadWarning = true;
				return true;
			}
			return false;
		});
		try
		{
			return unserialize($data);
		}
		finally
		{
			restore_error_handler();
		}
	}
}
