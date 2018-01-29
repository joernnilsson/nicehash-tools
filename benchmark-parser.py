import sys, os, re, json

# Parses the output of excavator-benchmark and stores results in benchark.json

results = {}

def getRate(rate, unit):
    if(unit == None):
        mul = 1
    elif(unit == "k"):
        mul = 1e3
    elif(unit == "M"):
        mul = 1e6
    elif(unit == "G"):
        mul = 1e9
    else:
        raise Exception("Unknown unit multiplier: "+unit)
    return rate * mul

for line in sys.stdin:
    line = line.strip()
    m1 = re.match(r"^([a-zA-Z0-9_]+)\:\s([0-9]*\.[0-9]+|[0-9]+)\s([a-zA-Z])?H/s$",line)
    m2 = re.match(r"^([a-zA-Z0-9_]+)\:\s([0-9]*\.[0-9]+|[0-9]+)\s([a-zA-Z])?H/s\s&\s([0-9]*\.[0-9]+|[0-9]+)\s([a-zA-Z])?H/s$",line)
    if(m1 is not None):
        results[m1.group(1)] = getRate(float(m1.group(2)), m1.group(3))
        #print(algo+": "+str(results[algo]))
    elif(m2 is not None):
        results[m2.group(1)] = [getRate(float(m2.group(2)), m2.group(3)), getRate(float(m2.group(4)), m2.group(5))]
    print(line)

with open('benchmark.json', "w") as f:
    f.write(json.dumps(results, sort_keys=True, indent=4))