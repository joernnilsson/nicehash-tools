import requests, time, os, sys
import logging
import re
import json

import urllib.error
import urllib.request

NICEHASH_TIMEOUT = 20


def multialgo_info():
    """Retrieves pay rates and connection ports for every algorithm from the NiceHash API."""
    response = urllib.request.urlopen('https://api.nicehash.com/api?method=simplemultialgo.info',
                                    None, NICEHASH_TIMEOUT)
    query = json.loads(response.read().decode('ascii')) #json.load(response)
    paying = {}
    ports = {}
    for algorithm in query['result']['simplemultialgo']:
        name = algorithm['name']
        paying[name] = float(algorithm['paying'])
        ports[name] = int(algorithm['port'])
    return paying, ports




