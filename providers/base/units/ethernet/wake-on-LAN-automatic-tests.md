# This is a file introducing wake-on-LAN automatic test jobs.
To perform wake-on-LAN automatic test, you will need following environment:
>A host running the wol-server.py, installed the wakeonlan, it will lisenten to the request from client. and send back the wakeonlan based on the MAC and delay and retry.

## # Work process of of the wake-on-LAN automatic test
    The DUT running checkbox will use the send the request to HOST, 
    HOST will receive the request and send the wakeonlan to DUT.
    DUT will receive the wakeonlan and wake up.
    The DUT running checkbox will check the DUT is wake up or not.
    If the DUT is wake up, the DUT running checkbox will send the success message to
    the HOST, HOST will receive the message and send the success message to the client.
    If the DUT is not wake up, the DUT running checkbox will send the fail message
    to the HOST, HOST will receive the message and send the fail message to the client.

## Test Case enviroment
    host:
    test:
        manifest:
        enviroment:

## id: ethernet/wol_S3_auto_{{ interface }}

