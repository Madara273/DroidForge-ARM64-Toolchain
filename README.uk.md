# DroidForge ARM64 Toolchain

[English](README.md) | **Українська**

Android-інструменти для Linux ARM64 та x86_64: `aapt2`, `aidl`, `zipalign`,
`split-select`, `adb`, `fastboot`. Встановіть готовий реліз для збірки APK
або зберіть інструменти з джерел.

Підтримується Linux/glibc: Kali, Debian/Ubuntu, Arch та ці дистрибутиви у WSL2.
Перевірено в Kali ARM64. У WSL2 використовуйте Linux-термінал і тримайте репозиторій у `~/`.

```sh
git clone https://github.com/Madara273/DroidForge-ARM64-Toolchain.git
cd DroidForge-ARM64-Toolchain
```

## Налаштування збірки APK

```sh
./setup-android.sh
. "$HOME/.config/droidforge/env.sh"
```

Встановлює або використовує наявні JDK та Android SDK, встановлює нативні
інструменти й налаштовує шлях Gradle до AAPT2. Типові версії: JDK 21,
платформа `android-37.0`, build-tools `36.0.0` та `37.0.0`.
Ліцензії SDK приймаються автоматично.

На ARM64 скрипт завантажує реліз `droidforge-linux-arm64-r01` із публічного
репозиторію. На x86_64 використовує інструменти Google SDK.
Якщо архів уже завантажено:

```sh
./setup-android.sh --archive "$HOME/droidforge-linux-arm64.tar.xz"
```

Після налаштування збирайте свій застосунок:

```sh
cd /шлях/до/android-застосунку
./gradlew assembleDebug
```

Корисні параметри:

```sh
./setup-android.sh --platform android-36 --build-tools 36.0.0
./setup-android.sh --java-version 17
./setup-android.sh --sdk "$HOME/Android/Sdk"
```

Наявні налаштування Java у Gradle зберігаються, якщо не задано `--java-version`.
SDK береться з `ANDROID_HOME` або `~/Android/Sdk`. Інструменти зберігаються
в `~/.local/share/droidforge/`. Перед зміною налаштувань і файлів SDK створюються
резервні копії. Для проєктів із NDK/CMake або іншими версіями SDK/JDK
потрібні відповідні залежності.

ARM64-реліз r01 потребує **GLIBC_2.38** та **GLIBCXX_3.4.32**. На старіших
системах збирайте з джерел. Gallery успішно зібрано з локальним архівом r01;
завантаження SDK/JDK з нуля та публічного релізу ще не перевірено повністю.

## Збірка інструментів із джерел

Для поточного процесора:

```sh
./build-all.sh
```

Залежності встановлюються автоматично через apt або pacman. Скрипт збирає,
перевіряє й пакує всі шість інструментів. Використовує доступні потоки CPU,
до 64; параметр `--jobs 4` обмежує паралельність. Повторний запуск використовує кеш.
Запускайте звичайним користувачем; sudo запитується для залежностей.
У root-середовищі NetHunter запускайте без sudo.

Для збірки ARM64 на x86_64 Linux або WSL2 встановіть Docker із Buildx і запустіть:

```sh
./docker-build-arm64.sh
```

Використовується ARM64-контейнер із QEMU на x86_64, тому збірка повільніша.
Якщо Docker повідомляє `exec format error`, один раз увімкніть емуляцію ARM64:

```sh
docker run --privileged --rm tonistiigi/binfmt --install arm64
```

Docker Desktop уже містить емуляцію. Повну Docker-збірку ще не перевірено.

## Результати та журнали

| | Локальна збірка | Docker-збірка |
| --- | --- | --- |
| Інструменти | `.forge/out/` | `.docker-cache/arm64/out/` |
| Журнал | `.forge/logs/build.log` | `.docker-cache/arm64/logs/build.log` |
| Архів | `dist/droidforge-linux-<arch>.tar.xz` | `dist/droidforge-linux-arm64.tar.xz` |

Прогрес збірки видно в терміналі. Архіви містять контрольні суми й ліцензії.
Кеш джерел обмежено **4,5 GiB**; SDK/JDK, образи Docker та результати компіляції
потребують додаткового місця. Загальне використання диска не обмежено 5 ГБ.

Окремі кроки та збірка вибраних інструментів:

```sh
./forge doctor
./forge fetch
./forge build --targets aapt2 adb --jobs 4
./forge verify
./forge package
```

## Структура проєкту

```text
bootstrap.sh, build-all.sh, setup-android.sh, docker-build-arm64.sh, forge
config/       # Налаштування збірки та зафіксовані джерела
docker/       # Dockerfile і контекст збірки
droidforge/   # Реалізація на Python
compat/       # Заголовки сумісності з Linux
tests/        # Тести Python і C++
```

## Джерела та ліцензія

База джерел: AOSP `platform-tools-35.0.2`, коміти Git зафіксовано.
Рецепти збірки: [lzhiyong/android-sdk-tools](https://github.com/lzhiyong/android-sdk-tools).
ADB `--fastdeploy` вимкнено. Результат призначений для Linux/glibc, зокрема
відповідного userland у NetHunter. Для Termux/bionic ці файли не підходять.

Copyright 2026 Madara273. Власні скрипти та код DroidForge: Apache-2.0.
Сторонні інструменти мають власні ліцензії.
Див. [LICENSE](LICENSE) та [NOTICE](NOTICE).
