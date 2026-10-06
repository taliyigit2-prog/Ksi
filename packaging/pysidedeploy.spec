[app]
title = KSI Local Studio Frozen Pilot
project_dir = ..
input_file = packaging/phase19_pilot.py
exec_directory = build/nuitka
project_file = 
icon = packaging/KSI-Local-Studio.icns

[python]
python_path =
packages = Nuitka==4.1.1
android_packages = 

[qt]
qml_files = 
excluded_qml_plugins = 
modules = Core,DBus,Gui,PrintSupport,Widgets
plugins = platforms,styles,imageformats,iconengines

[android]
wheel_pyside = 
wheel_shiboken = 
plugins = 

[nuitka]
macos.permissions = 
mode = standalone
extra_args = --quiet --noinclude-qt-translations --include-package=ksi_local --include-package=docx --include-package=pypdf --include-package=lingua --include-data-dir=config=config --include-data-dir=assets=assets --include-data-file=build/KSIOCR=bin/KSIOCR --nofollow-import-to=mlx_whisper,torch,torchaudio,transformers,gradio,modelscope,funasr

[buildozer]
mode = debug
recipe_dir = 
jars_dir = 
ndk_path = 
arch = 
