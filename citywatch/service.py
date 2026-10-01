"""Optional container worker. Not started by installing or running a preview."""
import os
import subprocess
import sys
import time


def main():
    interval = max(300, int(os.environ.get('CITYWATCH_INTERVAL_SECONDS', '3600')))
    send_email = os.environ.get('CITYWATCH_SEND_EMAIL', 'false').lower() == 'true'
    state = os.environ.get('CITYWATCH_STATE', '/app/var')
    config = os.environ.get('CITYWATCH_CONFIG', '/app/config/watch.json')
    print('CityWatch worker started; interval=%ds; email=%s' % (interval, send_email), flush=True)
    while True:
        print('Starting collection', flush=True)
        result = subprocess.run([sys.executable, '-m', 'citywatch', 'scan', '--state', state, '--config', config])
        # Valid partial results are deliverable, with the coverage warning in the digest.
        if send_email and result.returncode in (0, 2):
            subprocess.run([sys.executable, '-m', 'citywatch', 'send', '--state', state, '--config', config])
        print('Collection finished (exit %d); next scan in %ds' % (result.returncode, interval), flush=True)
        time.sleep(interval)


if __name__ == '__main__':
    main()
