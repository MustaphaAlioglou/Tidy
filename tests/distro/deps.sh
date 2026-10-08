set -e
. /etc/os-release
case " $ID $ID_LIKE " in
*" arch "*)
    pacman -Syu --noconfirm --needed python python-gobject gtk4 libadwaita pyside6 \
        xorg-server-xvfb ttf-dejavu shadow ;;
*" debian "*|*" ubuntu "*)
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y --no-install-recommends python3 python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 \
        xvfb xauth fonts-dejavu-core passwd
    apt-get install -y --no-install-recommends python3-pyside6.qtwidgets python3-pyside6.qtdbus \
        || echo "deps: no PySide6 packages on $PRETTY_NAME" ;;
*" fedora "*)
    dnf install -y python3 python3-gobject gtk4 libadwaita python3-pyside6 \
        xorg-x11-server-Xvfb dejavu-sans-fonts shadow-utils ;;
*" suse "*|*" opensuse "*)
    zypper -n install python3 python3-gobject python3-gobject-Gdk typelib-1_0-Gtk-4_0 \
        typelib-1_0-Adw-1 xorg-x11-server-Xvfb dejavu-fonts shadow
    zypper -n install python3-pyside6 || echo "deps: no PySide6 package on $PRETTY_NAME" ;;
*)
    echo "deps: unknown distro $ID" >&2; exit 1 ;;
esac
