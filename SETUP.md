# Spool Stock setup

Do these in order to run your own copy. Nothing here costs money.

## 1. GitHub

1. Create a repository, or fork this one. Make it **Public**, as free GitHub Pages needs that.
2. Open **Settings > Pages** and set **Source** to **GitHub Actions**.

Publishing the code is safe: your data, password and logos are not in it.

## 2. Firebase

1. Go to https://console.firebase.google.com and create a project. You can turn Google Analytics off.
2. Open **Firestore Database** and press **Create database**. Choose a location near you and **production mode**.
3. Open the **Rules** tab, replace everything with the contents of `firestore.rules`, and press **Publish**.
4. Open **Authentication**, press **Get started**, open **Sign-in method**, then enable **Email/Password**.
5. Open **Project settings** (the gear), scroll to **Your apps**, press the web icon `</>`, name it and register it. Don't tick Firebase Hosting.
6. Copy the `firebaseConfig` object.

## 3. Your settings

Fill in `site/config.js`:

| Setting | What goes in it |
|---|---|
| `siteUrl` | Your GitHub Pages address. Sticker QR codes use this, so set it before printing. |
| `repo` | Your repo, as `owner/name`. Setup links to its releases. |
| `readerUrl` | Your copy of Spool Reader, or leave it out. |
| `firebase` | The object you copied, leaving `apiKey` as `__FIREBASE_API_KEY__`. |

The Android and Windows apps open your site, so put the same address in `wrappers/android/capacitor.config.json` and `wrappers/windows/main.js`.

Then in the GitHub repo open **Settings > Secrets and variables > Actions**, press **New repository secret**, call it `FIREBASE_API_KEY` and paste in the `apiKey` value. It's added to `config.js` each time the site publishes.

Push to `main` and the site publishes itself.

## 4. First sign in

1. Open your site, enter your email and a password, then press **Create account**.
2. Back in Firebase, go to **Authentication > Settings > User actions** and untick **Enable create (sign-up)**, so nobody else can make an account.

Keep the password somewhere safe. You'll use it once on each device.

## 5. Set up your storage

Accounts start with no storages, so add what you own under **Setup > Add storage**.

1. Choose what it is. The type sets the defaults, such as 4 numbered spaces for a multi-material unit.
2. Name it and change anything that doesn't match yours.
3. Use **Group** to keep things together, such as a printer and what feeds it.
4. Use **How many like this** if you have several of the same.

Each storage gets one sticker, which goes in the queue on the Stickers page. To start again, use **Remove all storages** in Setup. Your spools stay in stock.

## 6. Apps

1. In the GitHub repo open **Actions > Build apps > Run workflow**.
2. When it finishes, open **Releases**. `SpoolStock.apk` and `SpoolStock.exe` are there.
3. **Android:** download the APK on your phone and allow installing from your browser when asked. Allow the camera the first time you scan.
4. **Windows:** run the exe. It isn't code signed, so if SmartScreen appears, choose **More info > Run anyway**.

Both apps open the website inside them, so site updates reach them without reinstalling.

## Orca plugin (Bambu printers)

1. Under **Setup > Printers**, add each printer with its serial number and what feeds it.
2. Under **Setup > Orca plugin**, press **Download the plugin**, and follow [its steps](orca-plugin/spoolstock/README.md) to install it in OrcaSlicer 2.5 and sign it in.

The printers need LAN mode on. Their IPs and access codes stay in the plugin on your PC.

## Using Spool Reader

Spool Reader is the separate Claude artifact for photos. Publish `reader/spool-reader.html` as an artifact to get your own copy.

1. **Read labels:** add photos, read them, check the results under To export.
2. **Download file** or **Copy** there, then in Spool Stock use **Add > Import from Spool Reader**.
3. **Identify:** in Spool Stock use **Setup > Copy stock list**, then load it in Spool Reader's Identify tab.

## Logos

Each logo only has to be found once. It's saved to your account, so every device and every future sticker pulls it from there.

- **Common brands** with a Simple Icons vector come through automatically.
- **Other brands:** open **Setup**, press **Find logo** and pick one if Wikimedia has it.
- **If nothing's found:** upload an SVG or PNG once, or crop it from a label photo in Spool Reader.

Every logo is turned solid black on transparent. No API keys are needed.

## Printing stickers

Print from a PC or a browser, at 100% or Actual size. Do a test on plain paper first using the outlines option.
