#!/usr/bin/python3

import logging
import threading
import time
import subprocess
import shlex
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder

app = FastAPI()

LOG_LEVEL = 'DEBUG'
logging.basicConfig(level=LOG_LEVEL)
logger = logging.getLogger(__name__)


@app.post("/")
async def testing(wol_request: dict):
    try:
        ret_server = tasker_main(wol_request)
        return JSONResponse(content=jsonable_encoder(ret_server),
                            status_code=200)
    except Exception as e:
        logger.critical(repr(e))


def send_wol_command(Wol_Info: dict):

    dut_mac = Wol_Info["DUT_MAC"]
    dut_ip = Wol_Info["DUT_IP"]
    wake_type = Wol_Info["wake_type"]

    command_dict = {
                    "g": f"wakeonlan {dut_mac}",
                    "a": f"ping {dut_ip}",
                   }

    try:
        logger.debug(f"Wake on lan command: {command_dict[wake_type]}")
        output = subprocess.check_output(shlex.split(command_dict[wake_type]))
        logger.debug({output})

    except subprocess.CalledProcessError as e:
        logger.error(f"Error occurred in tasker_main: {e}")
        return False

    except KeyError as e:
        logger.error(f"Error occurred in tasker_main: {e}")
        return False

    return True


def tasker_main(request: dict) -> dict:

    try:
        # Extracting necessary fields from the request
        dut_ip = request.get('DUT_IP')
        delay = request.get('delay')

        if not dut_ip or delay is None:
            logger.error("Missing required fields: DUT_IP or delay")
            return {'result': 'error', 'message': 'Missing required fields'}

        logger.info(f"Received request: {request}")
        logger.info(f"DUT_IP: {dut_ip}")

        # Starting the task in a separate thread
        thread = threading.Thread(target=run_task, args=(request, delay))
        thread.start()

        # Returning success response
        return {'result': 'success'}

    except Exception as e:
        logger.exception(f"Error occurred while processing the request: {e}")
        return {'result': 'error', 'message': str(e)}


def is_pingable(ip_address):
    try:
        #  use ping command to ping the host
        command = ["ping", "-c", "1", "-W", "1", ip_address]
        output = subprocess.check_output(command, stderr=subprocess.STDOUT,
                                         universal_newlines=True)
        logger.debug(f"ping: {output}")
        return True
    except subprocess.CalledProcessError:
        # print("ping:", output)
        logger.debug("An error occurred while ping the DUT: str{e}")
        return False


def run_task(data, delay):

    # dut_mac = data['DUT_MAC']
    dut_ip = data['DUT_IP']
    delay = data['delay']
    retry_times = data['retry_times']
    # wake_type = data['wake_type']

    for attempt in range(retry_times):
        # logger.info("threading:", dut_mac)
        logger.debug(f"retry times: {attempt}")
        time.sleep(delay)

        try:
            # send wol command to the dut_mac
            logger.debug("send wol command to the dut_mac")
            send_wol_command(data)

            # delay a little time, ping the DUT,
            # if not up, send wol command again
            logger.debug("ping DUT to see if it had been waked up")
            time.sleep(delay)
            # ping dut
            if is_pingable(dut_ip):
                logger.info(f"{dut_ip} is pingable, the DUT is back")
                # logger.info("ping DUT to see if it had been waked up")
                return True
            else:
                logger.info(f"{dut_ip} is NOT pingable, the DUT is not back.")

        except Exception as e:
            logger.error(f"Error occurred in tasker_main: {e}")

        # retry finished
    return False


if __name__ == "__main__":
    req = {
          "DUT_MAC": "00:00:00:00:00",
          "DUT_IP": "127.0.0.1",
          "delay": 60,
          "retry_times": 5,
          "wake_type": "g"
          }
    r1 = tasker_main(request=req)
