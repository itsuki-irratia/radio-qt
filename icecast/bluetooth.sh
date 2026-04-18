ffmpeg -f pulse -thread_queue_size 4096 -i "bluez_output.F8_DF_15_C7_1C_3D.a2dp-sink.monitor" -ac 2 -ar 48000 -c:a libmp3lame -b:a 128k -content_type audio/mpeg -f mp3 "icecast://source:hackme@localhost:8000/radio.mp3"

