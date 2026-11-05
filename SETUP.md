# Spool Stock setup

Do these in order. Nothing here costs money.

## 1. GitHub

1. Sign in to GitHub as `jwilss` and create a new repository called `spool-stock`. Make it **Public** (free GitHub Pages needs that). Leave it empty.
2. In the repo, open **Settings > Pages** and set **Source** to **GitHub Actions**.
3. Tell Claude the repo is ready and it can push these files for you. Or upload the contents of this folder yourself, keeping the `.github` folder.

Publishing the code is safe: your data, password and logos are not in it.

## 2. Firebase

1. Go to https://console.firebase.google.com and sign in with a Google account.
2. **Add project**, call it `spool-stock`. You can turn Google Analytics off.
3. Open https://console.firebase.google.com/project/spool-stock/firestore and press **Create database**. Choose a location near you, such as `europe-west2` (London), and **production mode**.
4. Open the **Rules** tab, replace everything with the contents of `firestore.rules`, and press **Publish**.
5. Open https://console.firebase.google.com/project/spool-stock/authentication, press **Get started**, open **Sign-in method**, then enable **Email/Password**.
6. Open **Project settings** (the gear), scroll to **Your apps**, press the web icon `</>`, name it and register it. Don't tick Firebase Hosting.
7. Copy the `firebaseConfig` object. Send it to Claude, or paste it into `site/config.js` in place of `null`. It's safe to share: `firestore.rules` is what protects your data.

## 3. First sign in

1. Open https://jwilss.github.io/spool-stock/
2. Enter your email and a password, then press **Create account**. Your storages are added automatically.
3. Back in Firebase, go to **Authentication > Settings > User actions** and untick **Enable create (sign-up)**, so nobody else can make an account.

Keep the password somewhere safe. You'll use it once on each device.

## 4. Apps

1. In the GitHub repo open **Actions > Build apps > Run workflow**.
2. When it finishes, open **Releases**. `SpoolStock.apk` and `SpoolStock.exe` are there.
3. **Android:** download the APK on your phone and allow installing from your browser when asked. Allow the camera the first time you scan.
4. **Windows:** run the exe. It isn't code signed, so if SmartScreen appears, choose **More info > Run anyway**.

Both apps open the website inside them, so site updates reach them without reinstalling.

## Using Spool Reader

Spool Reader is the separate Claude artifact for photos. It's linked in Setup.

1. **Read labels:** add photos, read them, check the results under To export.
2. **Download file** or **Copy** there, then in Spool Stock use **Add > Import from Spool Reader**.
3. **Identify:** in Spool Stock use **Setup > Copy stock list**, then load it in Spool Reader's Identify tab.

## Printing stickers

Print from a PC or a browser, at 100% or Actual size. Do a test on plain paper first using the outlines option.
