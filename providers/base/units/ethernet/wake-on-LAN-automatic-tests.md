# This is a file introducing wake-on-LAN automatic test jobs.

  To make the test of wake-on-LAN automatic, we need:
  
  The device under test (DUT) obtains its own network interface's MAC and IP address, retrieves the wake-on-LAN server's IP and port from environment variables, sends the IP and MAC to the wake-on-LAN server, it records the current timestamp and suspends itself after receiving a successful response from the server.

  A wake-on-LAN HTTP server that receives requests from the device under test (DUT), extracts the DUT's MAC and IP addresses from the request, and then sends a wake-on-LAN command to the DUT in an attempt to power it on.

  Once the DUT wakes up, it compares the previously recorded timestamp with the time when the system last exited suspend mode. If the system wakes up within a reasonable timeframe, it can be inferred that the wake-up was triggered by the wake-on-LAN request, indicating a successful test. Otherwise, the system was woken up by the RTC, it implies that the wake-on-LAN attempt failed.

## id: ethernet/wol_S3_auto_{{ interface }}

## Test Case enviroment
    WOL server:
      1. apt install wakeonlan
      2. running the python script wol_server.py

    DUT:
      manifest:
        - has_ethernet_wake_on_lan_support
        - has_ethernet_wake_on_lan_support
      enviroment : 
        - SERVER_WAKE_ON_LAN
        - WAKE_ON_LAN_DELAY
        - WAKE_ON_LAN_RETRY

## Work process of the wake-on-LAN automatic test
  1. The DUT gets its own NIC's MAC and IP, fetches WOL server info from environment variables, sends data to the server, receives a success response, records timestamp, sets rtcwake, and suspends.
  2. The WOL server receives DUT requests, extracts MAC, IP, delay, and retry count. After sending a success response, it waits, sends a WOL command, waits, and pings. If the ping fails, it retries up to the specified retry times.
  3. After system resume up, the DUT compares the resume time to the stored timestamp. If the elapsed time is between delay and 1.5(delay*retry), WOL is assumed; otherwise, an RTC wake-up is inferred.




