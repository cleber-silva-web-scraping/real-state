FROM ubuntu:22.04 AS base

RUN echo 'APT::Install-Suggests "0";' >> /etc/apt/apt.conf.d/00-docker
RUN echo 'APT::Install-Recommends "0";' >> /etc/apt/apt.conf.d/00-docker

RUN echo "Acquire::Check-Valid-Until \"false\";\nAcquire::Check-Date \"false\";" | cat > /etc/apt/apt.conf.d/10no--check-valid-until

RUN export DEBIAN_FRONTEND=noninteractive 


RUN apt-get update  -y --no-install-recommends
RUN apt install -y --no-install-recommends tzdata


RUN apt-get update && apt install -y --no-install-recommends tzdata

RUN dpkg-reconfigure -f noninteractive tzdata
# Install packages
RUN export DEBIAN_FRONTEND=noninteractive

RUN apt update -q && apt-get install -y --no-install-recommends \
    wget curl rsync netcat mg vim bzip2 zip unzip gnupg2 \
    libx11-6 libxcb1 libxau6 \
    lxde lxterminal tightvncserver xvfb dbus-x11 x11-utils \
    xfonts-base xfonts-75dpi xfonts-100dpi \
    libssl-dev \
    xdotool \
    x11vnc

RUN apt-get install -y \
    python3 python3-pip python3-xlib python3-tk python3.10-venv  python3.10-distutils python3.10-dev \
    novnc \
    websockify \
    net-tools openssh-client git scrot gnome-screenshot \
    tesseract-ocr tesseract-ocr-eng

RUN apt-get install  mesa-utils \
    libgl1-mesa-dri \
    libgl1-mesa-glx \
    x11-xserver-utils \
    gsettings-desktop-schemas \
    xz-utils \
    -y \
    --no-install-recommends

RUN wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | apt-key add -  
RUN echo "deb http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google.list
RUN apt-get update && apt-get -y install google-chrome-stable

RUN wget -qO- https://deb.opera.com/archive.key | gpg --dearmor > /usr/share/keyrings/opera.gpg
RUN echo "deb [signed-by=/usr/share/keyrings/opera.gpg] https://deb.opera.com/opera-stable/ stable non-free" > /etc/apt/sources.list.d/opera.list
RUN echo "opera-stable opera-stable/add-deb-source boolean true" | debconf-set-selections
RUN DEBIAN_FRONTEND=noninteractive apt-get update && DEBIAN_FRONTEND=noninteractive apt-get -y install opera-stable


RUN useradd -ms /bin/bash rpa
USER rpa

RUN python3 -m venv /home/rpa/.venv
ENV PATH=/home/rpa/.venv/bin:$PATH

RUN export XKL_XMODMAP_DISABLE=1
RUN export USER=root
RUN export DISPLAY=:1
RUN mkdir -p /home/rpa/.vnc
RUN echo  "/usr/bin/startlxde" > /home/rpa/.vnc/xstartup
RUN chmod a+x /home/rpa/.vnc/xstartup
RUN touch /home/rpa/.vnc/passwd
RUN /bin/bash -c "echo -e 'contem1g\ncontem1g\nn' | vncpasswd" > /home/rpa/.vnc/passwd
RUN chmod 400 /home/rpa/.vnc/passwd
RUN chmod go-rwx /home/rpa/.vnc
RUN touch /home/rpa/.Xauthority

ENV DISPLAY=:1
EXPOSE 5901
EXPOSE 6901
EXPOSE 9222

FROM base
LABEL org.opencontainers.image.title="zillow"
LABEL project="zillow"
RUN . /home/rpa/.venv/bin/activate && pip3 install --no-cache-dir opencv-python-headless numpy mss pyautogui pytesseract
COPY ./entrypoint.sh /home/rpa/entrypoint.sh
COPY ./zillow_scraper /home/rpa/zillow_scraper
COPY ./.env /home/rpa/.env
USER root

RUN chmod a+x /home/rpa/entrypoint.sh

COPY ./chrome-agents/ /home/rpa/chrome-agents/
COPY ./chrome-policies.json /etc/opt/chrome/policies/managed/
RUN chown rpa:rpa /home/rpa/.env /home/rpa/chrome-agents -R

USER rpa

ENV POC_MODE=1
ENV POC_TARGET_URL="https://www.zillow.com/homes/for_rent/"
ENV POC_MAX_RETRIES=5
ENV POC_RETRY_DELAY_SECONDS=8
ENV POC_OUTPUT_FILE="/home/rpa/out/product-links.json"
ENV POC_CHECKPOINT_FILE="/home/rpa/out/checkpoint.json"
ENV POC_DEBUG_DIR="/home/rpa/out/debug"
ENV POC_REDO_LAST_PAGE_ON_RESUME=1
ENV POC_CHROME_USER_DATA_DIR="/home/rpa/chrome-user-data"
ENV POC_BROWSER_SEQUENCE="chrome,opera"
ENV POC_BROWSER_ROTATION_ENABLED=1
ENV POC_BROWSER_ROTATION_INTERVAL_SECONDS=300
ENV POC_BROWSER_USER_DATA_BASE_DIR="/home/rpa/browser-user-data"
ENV POC_BROWSER_DISABLE_SANDBOX=0
ENV POC_BROWSER_DISABLE_GPU=0
ENV POC_BROWSER_REMOTE_DEBUGGING_ENABLED=0
ENV POC_BROWSER_HIDE_AUTOMATION=1
ENV POC_BROWSER_VERBOSE_LOGS=0
ENV POC_GUI_CLICK_DEMO=0
ENV POC_GUI_FORCE_BROWSER_COMPAT=0
ENV POC_PRODUCTS_PAGES_PER_RUN=0
ENV POC_PRODUCTS_MAX_ITEMS_PER_RUN=0
ENV POC_PRODUCT_DELAY_SECONDS=2
ENV POC_PRODUCTS_CSV_FILE="/home/rpa/out/Zillow_{timestamp}.csv"
ENV POC_POSTPROCESS_ONLY=0
ENV POC_EXIT_AFTER_FINISH=1

CMD [ "/home/rpa/entrypoint.sh" ]
