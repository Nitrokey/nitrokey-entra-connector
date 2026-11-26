import argparse
import os
import shutil
import traceback

import requests
from azure.identity import InteractiveBrowserCredential

parser = argparse.ArgumentParser()
parser.add_argument("--tenant-id", dest="tenant_id", help='Tenant ID (This is a GUID which represents the "Directory ID" of the AzureAD tenant into which you want to create the apps')
parser.add_argument("--azure-environment-name", dest="azure_environment_name", help="Azure environment to use while running the script. Default = Global")
args = parser.parse_args()
tenant_id = args.tenant_id
azure_environment_name = args.azure_environment_name


def replace_in_line(line, key, value):
    """Takes a single line, matches a key value and replaces it with the replacement value"""
    return line.replace(key, value)


def update_text_file(config_file_path, dictionary):
    """Replaces the placeholders in a text file using a dictionary of keys and replacements"""
    # Check if text file exists. If not, copy .env.sample.
    if not os.path.exists(config_file_path):
        shutil.copy(config_file_path + ".sample", config_file_path)
    with open(config_file_path) as f:
        lines = f.read().splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        for key in dictionary:
            if key in line:
                lines[index] = replace_in_line(line, key, dictionary[key])
        index += 1

    with open(config_file_path, "w") as f:
        f.write("\n".join(lines) + "\n")


def configure_applications(tenant_id, azure_environment_name):
    """Primary entry method to create and configure app registrations"""
    is_open_ssl = "N"  # temporary disable open certificate creation

    if not azure_environment_name:
        azure_environment_name = "Global"

    # Connect to the Microsoft Graph API
    print("Connecting to Microsoft Graph")
    scopes = [
        "https://graph.microsoft.com/User.Read.All",
        "https://graph.microsoft.com/Organization.Read.All",
        "https://graph.microsoft.com/Application.ReadWrite.All",
    ]
    if not tenant_id:
        credential = InteractiveBrowserCredential()
    else:
        credential = InteractiveBrowserCredential(tenant_id=tenant_id)
    token = credential.get_token(*scopes)

    base = "https://graph.microsoft.com/v1.0"
    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {token.token}"

    def graph(method, path, **kwargs):
        r = session.request(method, base + path, **kwargs)
        if not r.ok:
            raise Exception(f"{method} {path}: {r.status_code} {r.text}")
        return r.json() if r.content else {}

    # Get the user running the script
    current_user_principal_name = graph("GET", "/me")["userPrincipalName"]
    user = graph("GET", "/users", params={"$filter": f"UserPrincipalName eq '{current_user_principal_name}'"})["value"][0]

    # get the tenant we signed in to
    tenant = graph("GET", "/organization")["value"][0]
    tenant_name = tenant["displayName"]

    verified_domain = next(d for d in tenant["verifiedDomains"] if d.get("isDefault") is True)
    verified_domain_name = verified_domain["name"]
    tenant_id = tenant["id"]

    print("Connected to Tenant {0} ({1}) as account '{2}'. Domain is '{3}'".format(
        tenant["displayName"], tenant["id"], current_user_principal_name, verified_domain_name))

    # Create the webApp AAD application
    print("Creating the AAD application (WebApp)")
    # create the application
    web_app_aad_application = graph("POST", "/applications", json={
        "displayName": "WebApp",
        "web": {
            "redirectUris": ["http://localhost:5000/getAToken"],
            "homePageUrl": "https://localhost:5000/",
            "logoutUrl": "https://localhost:5000/logout",
        },
        "signInAudience": "AzureADandPersonalMicrosoftAccount",
    })

    # add a secret to the application
    pwd_credential = graph("POST", f"/applications/{web_app_aad_application['id']}/addPassword", json={})
    web_app_app_key = pwd_credential["secretText"]

    current_app_id = web_app_aad_application["appId"]
    current_app_object_id = web_app_aad_application["id"]

    tenant_name = graph("GET", f"/applications/{current_app_object_id}")["publisherDomain"]

    # create the service principal of the newly created application
    web_app_service_principal = graph("POST", "/servicePrincipals", json={
        "appId": current_app_id,
        "tags": ["WindowsAzureActiveDirectoryIntegratedApp"],
    })

    # add the user running the script as an app owner if needed
    owner = graph("GET", f"/applications/{current_app_object_id}/owners")["value"]
    if not owner:
        graph("POST", f"/applications/{current_app_object_id}/owners/$ref", json={
            "@odata.id": f"{base}/directoryObjects/{user['id']}"})
        print("'{0}' added as an application owner to app '{1}'".format(
            user["userPrincipalName"], web_app_service_principal["displayName"]))
    print("Done creating the webApp application (WebApp)")

    # URL of the AAD application in the Azure portal
    web_app_portal_url = ("https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationMenuBlade/~/Overview/appId/"
                          + current_app_id + "/isMSAApp~/false")

    # print the registered app portal URL for any further navigation
    print("✔ Successfully registered and configured that app registration for 'WebApp' at\n "
          + web_app_portal_url)

    # Update config file for 'webApp'
    config_file = os.path.join(os.getcwd(), "..", ".env")

    dictionary = {
        "<client id>": web_app_aad_application["appId"],
        "<client secret>": web_app_app_key,
        "<tenant id>": tenant_name,
    }

    print(f"⚠ Updating the sample config '{config_file}' with the following config values:")
    print(dictionary)
    print("-----------------")

    update_text_file(config_file, dictionary)

    if is_open_ssl == "Y":
        print("✔ " + "-" * 96)
        print("You have generated certificate using OpenSSL so follow below steps: ")
        print("Install the certificate on your system from current folder.")
        print("✔ " + "-" * 96)


# Run interactively (will ask you for the tenant ID)

try:
    configure_applications(tenant_id, azure_environment_name)
except Exception as e:
    traceback.print_exc()
    message = e
    print(f"WARNING: {e}")
    print(f"✘ Unable to register apps. Error is {message}.")
print("Disconnecting from tenant")