ElevenLabs

腳本：ChatGPT 製作
語音：ElevenLab
圖片：Nano Banana Pro
音樂：ElevenLab
Source: GUTENBERG - AESOP'S FABLES

Voice Model
* Yui - Delicate, Graceful, and Soothing
* Stacy - Young, Sweet, and Cute
* Anna Su - Casual

# Prompt
翻譯故事給6歲小朋友聽。
故事 template:
==
...

各位親愛的小朋友們大家好，我是 Lumi 姐姐。
今天要和你分享一個小故事。

<故事本體>

<給小朋友一個小小的反思>

今天的故事，就先說到這裡。讓這個小小的故事，在你心裡慢慢發光。我是 Lumi 姐姐，我們下次再見。
==
英文本文:


# mix
ffmpeg -i voice.mp3 -i music.mp3 \
-filter_complex "\
[1:a]afade=t=in:st=0:d=2,afade=t=out:st=7:d=3,volume=0.5[music]; \
[music]atrim=0:10[mintro]; \
[0:a]adelay=10000|10000[voice]; \
[mintro][voice]amix=inputs=2:duration=longest" \
-c:a libmp3lame -q:a 2 \
final.mp