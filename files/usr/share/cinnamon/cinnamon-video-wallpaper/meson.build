# Video wallpaper service

# Configure the service script
configure_file(
    input: 'cinnamon-video-wallpaper.in',
    output: 'cinnamon-video-wallpaper',
    configuration: config_js_conf,
    install_dir: 'share/cinnamon/cinnamon-video-wallpaper/'
)

# Install the Python module
install_data(
    'cinnamon-video-wallpaper.py',
    install_dir: 'share/cinnamon/cinnamon-video-wallpaper/'
)
