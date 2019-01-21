import logging
import overclock
import time
import json

class OcSpec:
    def __init__(self, gpu_clock=None, mem_clock=None, power=None):
        self.gpu_clock = gpu_clock
        self.mem_clock = mem_clock
        self.power = power

    def equals(self, other):
        return self.gpu_clock == other.gpu_clock and self.mem_clock == other.mem_clock and self.power == other.power
    
    def keys(self):
        return ["gpu_clock", "mem_clock", "power"]

    def __str__(self):

        spec = [("%s: %i" % (k, self.__getattribute__(k))) for k in self.keys() if self.__getattribute__(k) != None]
        return "None" if len(spec) == 0 else ", ".join(spec)

class OcStrategy:
    def __init__(self, device_uuid, algo):
        self.device_uuid = device_uuid
        self.algo = algo
        
    def get_spec(self):
        pass
    
    def refresh(self):
        pass

class OcStrategyStatic(OcStrategy):
    def __init__(self, device_uuid, algo, oc_spec):
        OcStrategy.__init__(self, device_uuid, algo)
        self.spec = oc_spec
    
    def get_spec(self):
        return self.spec

class OcStrategyFile(OcStrategy):
    def __init__(self, device_uuid, algo, filename):
        OcStrategy.__init__(self, device_uuid, algo)
        self.filename = filename
        self.oc_config = None

        self.refresh()
    
    def refresh(self):
        self.oc_config = json.load(open(self.filename))
    
    def get_spec(self):
        spec = OcSpec()

        paths = [
            [self.device_uuid, self.algo], 
            [self.device_uuid, "default"], 
            ["default", self.algo], 
            ["default", "default"]
        ]

        for e in spec.keys():
            for p in paths:
                if p[0] in self.oc_config and p[1] in self.oc_config[p[0]] and e in self.oc_config[p[0]][p[1]]:
                    spec.__setattr__(e, self.oc_config[p[0]][p[1]][e])
                    break

        return spec

class OcSession:
    class State:
        INACTIVE = "inactive"
        WARMUP_1 = "warmup_1"
        WARMUP_2 = "warmup_2"
        ACTIVE = "active"
        FINISHING = "finishing"

    
    TIME_WARMUP_1 = 2
    TIME_WARMUP_2 = 20
    BENCHMARK_MIN_LENGTH = 100

    def __init__(self, strategy, dev, database, excavator):
        
        self.strategy = strategy
        self.dev = dev
        self.applied_oc = OcSpec()
        self.database = database
        self.excavator = excavator

        self.avg_full = 0
        self.benchmark_result = {
            "length": 0,
            "avg_full": 0,
            "current_speed": 0
        }

        self._set_state(self.State.INACTIVE)
        self.reset_timer()
    
    def end(self):
        self.set_finishing()

    def reset_timer(self):
        self.state_time_start = time.time()
        self.state_time_last = self.state_time_start

    def _set_state(self, state):
        self.state = state
        logging.info("[%s] Oc session state: %s", self.strategy.device_uuid, state)

    def set_warmup_1(self):
        self._set_state(self.State.WARMUP_1)
        self.reset_timer()

    def set_warmup_2(self):
        self._set_state(self.State.WARMUP_2)
        self.reset_timer()
        self.overclock()
    
    def set_active(self):
        self._set_state(self.State.ACTIVE)
        self.excavator.device_speed_reset(self.strategy.device_uuid)
        self.reset_timer()
        # Reset speed measurement?

    def set_finishing(self):

        # Write to db if state is ACTIVE
        dev_power = 0
        dev_clock = 0
        dev_power = 0
        
        try:
            dev_clock = self.dev.get_clock_offset()
        except overclock.NotSupportedException:
            dev_clock = 0
        
        try:
            dev_mem = self.dev.get_memory_offset()
        except overclock.NotSupportedException:
            dev_mem = 0

        try:
            dev_power = self.dev.get_power_offset()
        except overclock.NotSupportedException:
            dev_power = 0

        if self.database and \
                self.state == self.State.ACTIVE and \
                self.benchmark_result["length"] > self.BENCHMARK_MIN_LENGTH:
            logging.debug("Saving benchmark result: %s", str(self.benchmark_result))
            self.database.save(self.strategy.algo, 
                self.strategy.device_uuid, 
                "excavator", "1.5.11", 
                self.benchmark_result["avg_full"], 
                dev_power, 
                dev_clock, 
                dev_mem, 
                True, 
                self.benchmark_result["length"])

        self._set_state(self.State.FINISHING)
        self.reset_timer()
        self.reset_overclock()

    def get_speeds(self):
        return self.benchmark_result["avg_full"]

    def get_result(self):
        return self.benchmark_result

    def loop_active(self, current_speed):
        now = time.time()
        time_prev = self.state_time_last - self.state_time_start
        time_cuml = now - self.state_time_start
        time_step = now - self.state_time_last
        self.state_time_last = now

        self.avg_full = (self.avg_full * time_prev + current_speed * time_step) / time_cuml

        self.benchmark_result = {
            "length": time_cuml,
            "avg_full": self.avg_full,
            "current_speed": current_speed
        }

    def loop(self, current_speed):

        if self.state == self.State.INACTIVE:
            if time.time() - self.state_time_start > 0:
                self.set_warmup_1()
        if self.state == self.State.WARMUP_1:
            if time.time() - self.state_time_start > self.TIME_WARMUP_1:
                self.set_warmup_2()
        if self.state == self.State.WARMUP_2:
            if time.time() - self.state_time_start > self.TIME_WARMUP_2:
                self.set_active()
        if self.state == self.State.ACTIVE:
            self.loop_active(current_speed)

    def overclock(self):
        self.strategy.refresh()
        spec = self.strategy.get_spec()

        logging.info("[%s] Applying Oc: %s", self.strategy.device_uuid, spec)

        if not self.applied_oc.equals(spec):

            if spec.gpu_clock:
                self.dev.set_clock_offset(spec.gpu_clock)
                logging.info("overclocking device %i, gpu_clock: %s" % (self.dev.device_number, str(spec.gpu_clock)))
            if spec.mem_clock:
                self.dev.set_memory_offset(spec.mem_clock)
                logging.info("overclocking device %i, mem_clock: %s" % (self.dev.device_number, str(spec.mem_clock)))
            if spec.power:
                try:
                    self.dev.set_power_offset(spec.power)
                    logging.info("overclocking device %i, power: %s" % (self.dev.device_number, str(spec.power)))
                except:
                    pass
            self.applied_oc = spec

    def reset_overclock(self):
        logging.info("[%s] Resetting Oc", self.strategy.device_uuid)

        # Reset overclocking
        if self.applied_oc.gpu_clock:
            self.dev.set_clock_offset(0)
            logging.info("overclocking device %i, gpu_clock: %s" % (self.dev.device_number, str(0)))
        if self.applied_oc.mem_clock:
            self.dev.set_memory_offset(0)
            logging.info("overclocking device %i, mem_clock: %s" % (self.dev.device_number, str(0)))
        if self.applied_oc.power:
            try:
                self.dev.set_power_offset(0)
                logging.info("overclocking device %i, power: %s" % (self.dev.device_number, str(0)))
            except:
                pass
        self.dev.unset_performance_mode()

        self.applied_oc = OcSpec()