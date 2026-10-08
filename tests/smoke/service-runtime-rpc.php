<?php
// Runtime probes intentionally include synchronous daemon work lasting over 5s.
$serviceRpcTimeout = 30;
require __DIR__ . '/service-rpc.php';
