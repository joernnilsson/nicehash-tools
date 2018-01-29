import sys, os, re, json

# Parses the output of excavator-benchmark and stores results in benchark.json

results = {}

for line in sys.stdin:
    line = line.strip()
    m = re.match(r"^([a-zA-Z0-9]+)\:\s([0-9]*\.[0-9]+|[0-9]+)\s([a-zA-Z])?H/s$",line)
    if(m is not None):
        algo = m.group(1)
        rate = float(m.group(2))
        unit = m.group(3)
        if(unit == None):
            mul = 1
        elif(unit == "k"):
            mul = 1e3
        elif(unit == "M"):
            mul = 10e6
        elif(unit == "G"):
            mul = 10e9
        else:
            raise Exception("Unknown unit multiplier: "+unit)
        results[algo] = rate * mul
        #print(algo+": "+str(results[algo]))
    print(line)

with open('benchmark.json', "w") as f:
    f.write(json.dumps(results, sort_keys=True, indent=4))