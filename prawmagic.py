FETCHES = 16

def speed_up():
    _modify_rate_limit()
    _use_prefetch()

# use exponentially decaying rate limiting to enhance burst performance
def _modify_rate_limit():
    from prawcore.rate_limit import RateLimiter
    import time

    if not hasattr(RateLimiter, "update_without_mod"):
        RateLimiter.update_without_mod = RateLimiter.update

    def update_with_mod(self, response_headers):
        RateLimiter.update_without_mod(self, response_headers)
        if self.next_request_timestamp != self.reset_timestamp:
            now = time.time()
            delta = self.next_request_timestamp - now
            self.next_request_timestamp = now + delta / FETCHES

    RateLimiter.update = update_with_mod

# patch praw.reddit.Reddit for parallel prefetch
def _use_prefetch():
    from praw.reddit import Reddit
    from praw.const import API_PATH
    from concurrent.futures import ThreadPoolExecutor
    from rx import from_iterable
    from rx.scheduler import ThreadPoolScheduler
    from rx.operators import map, buffer_with_count, finally_action
    from queue import Queue

    if not hasattr(Reddit, "info_without_prefetch"):
        Reddit.info_without_prefetch = Reddit.info

    def info_with_prefetch(self, fullnames=None, url=None):
        def info_generator(self, fullnames):
            with ThreadPoolExecutor() as e:
                queue = Queue(FETCHES)
                e.submit(lambda: from_iterable(fullnames).pipe(
                    buffer_with_count(100),
                    map(lambda chunk: {"id": ",".join(chunk)}),
                    map(lambda params: e.submit(lambda: self.get(API_PATH["info"], params=params))),
                    map(lambda future: queue.put(future)),
                    finally_action(lambda: queue.put(None))
                ).subscribe())
                while True:
                    response = queue.get()
                    if not response:
                        break
                    yield from response.result()
                       
        result = Reddit.info_without_prefetch(self, fullnames, url) # checks arguments
        return info_generator(self, fullnames) if fullnames else result

    Reddit.info = info_with_prefetch

# patch praw to retain JSON from Reddit API
def save_json():
    from praw.models.base import PRAWBase
    if not hasattr(PRAWBase, "__init_without_json__"):
        PRAWBase.__init_without_json__ = PRAWBase.__init__

    def init_with_json(self, reddit, _data):
        PRAWBase.__init_without_json__(self, reddit, _data)
        self.json = _data

    PRAWBase.__init__ = init_with_json

# track Reddit rate limiting
from threading import Lock
from ipywidgets import Output
from prawcore import Requestor
from IPython.display import display

rate_limit_lock = Lock()
rate_limit_output = Output()

def display_rate_limit():
    display(rate_limit_output)

class RateLimitLoggingRequestor(Requestor):
    def request(self, *args, **kwargs):
        response = super().request(*args, **kwargs)
        headers = response.headers
        with rate_limit_lock:
            rate_limit_output.clear_output()
            with rate_limit_output:
                print({k:headers[k] for k in headers if k.startswith("x-ratelimit")})
            return response
