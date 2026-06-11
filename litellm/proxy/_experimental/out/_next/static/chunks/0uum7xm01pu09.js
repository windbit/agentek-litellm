(globalThis.TURBOPACK||(globalThis.TURBOPACK=[])).push(["object"==typeof document?document.currentScript:void 0,599724,936325,e=>{"use strict";var t=e.i(95779),o=e.i(444755),r=e.i(673706),a=e.i(271645);let i=a.default.forwardRef((e,i)=>{let{color:n,className:l,children:s}=e;return a.default.createElement("p",{ref:i,className:(0,o.tremorTwMerge)("text-tremor-default",n?(0,r.getColorClassNames)(n,t.colorPalette.text).textColor:(0,o.tremorTwMerge)("text-tremor-content","dark:text-dark-tremor-content"),l)},s)});i.displayName="Text",e.s(["default",0,i],936325),e.s(["Text",0,i],599724)},653496,e=>{"use strict";var t=e.i(721369);e.s(["Tabs",()=>t.default])},304967,e=>{"use strict";var t=e.i(290571),o=e.i(271645),r=e.i(480731),a=e.i(95779),i=e.i(444755),n=e.i(673706);let l=(0,n.makeClassName)("Card"),s=o.default.forwardRef((e,s)=>{let{decoration:d="",decorationColor:c,children:p,className:g}=e,m=(0,t.__rest)(e,["decoration","decorationColor","children","className"]);return o.default.createElement("div",Object.assign({ref:s,className:(0,i.tremorTwMerge)(l("root"),"relative w-full text-left ring-1 rounded-tremor-default p-6","bg-tremor-background ring-tremor-ring shadow-tremor-card","dark:bg-dark-tremor-background dark:ring-dark-tremor-ring dark:shadow-dark-tremor-card",c?(0,n.getColorClassNames)(c,a.colorPalette.border).borderColor:"border-tremor-brand dark:border-dark-tremor-brand",(e=>{if(!e)return"";switch(e){case r.HorizontalPositions.Left:return"border-l-4";case r.VerticalPositions.Top:return"border-t-4";case r.HorizontalPositions.Right:return"border-r-4";case r.VerticalPositions.Bottom:return"border-b-4";default:return""}})(d),g)},m),p)});s.displayName="Card",e.s(["Card",0,s],304967)},629569,e=>{"use strict";var t=e.i(290571),o=e.i(95779),r=e.i(444755),a=e.i(673706),i=e.i(271645);let n=i.default.forwardRef((e,n)=>{let{color:l,children:s,className:d}=e,c=(0,t.__rest)(e,["color","children","className"]);return i.default.createElement("p",Object.assign({ref:n,className:(0,r.tremorTwMerge)("font-medium text-tremor-title",l?(0,a.getColorClassNames)(l,o.colorPalette.darkText).textColor:"text-tremor-content-strong dark:text-dark-tremor-content-strong",d)},c),s)});n.displayName="Title",e.s(["Title",0,n],629569)},994388,e=>{"use strict";var t=e.i(290571),o=e.i(829087),r=e.i(271645);let a=["preEnter","entering","entered","preExit","exiting","exited","unmounted"],i=e=>({_s:e,status:a[e],isEnter:e<3,isMounted:6!==e,isResolved:2===e||e>4}),n=e=>e?6:5,l=(e,t,o,r,a)=>{clearTimeout(r.current);let n=i(e);t(n),o.current=n,a&&a({current:n})};var s=e.i(480731),d=e.i(444755),c=e.i(673706);let p=e=>{var o=(0,t.__rest)(e,[]);return r.default.createElement("svg",Object.assign({},o,{xmlns:"http://www.w3.org/2000/svg",viewBox:"0 0 24 24",fill:"currentColor"}),r.default.createElement("path",{fill:"none",d:"M0 0h24v24H0z"}),r.default.createElement("path",{d:"M18.364 5.636L16.95 7.05A7 7 0 1 0 19 12h2a9 9 0 1 1-2.636-6.364z"}))};var g=e.i(95779);let m={xs:{height:"h-4",width:"w-4"},sm:{height:"h-5",width:"w-5"},md:{height:"h-5",width:"w-5"},lg:{height:"h-6",width:"w-6"},xl:{height:"h-6",width:"w-6"}},u=(e,t)=>{switch(e){case"primary":return{textColor:t?(0,c.getColorClassNames)("white").textColor:"text-tremor-brand-inverted dark:text-dark-tremor-brand-inverted",hoverTextColor:t?(0,c.getColorClassNames)("white").textColor:"text-tremor-brand-inverted dark:text-dark-tremor-brand-inverted",bgColor:t?(0,c.getColorClassNames)(t,g.colorPalette.background).bgColor:"bg-tremor-brand dark:bg-dark-tremor-brand",hoverBgColor:t?(0,c.getColorClassNames)(t,g.colorPalette.darkBackground).hoverBgColor:"hover:bg-tremor-brand-emphasis dark:hover:bg-dark-tremor-brand-emphasis",borderColor:t?(0,c.getColorClassNames)(t,g.colorPalette.border).borderColor:"border-tremor-brand dark:border-dark-tremor-brand",hoverBorderColor:t?(0,c.getColorClassNames)(t,g.colorPalette.darkBorder).hoverBorderColor:"hover:border-tremor-brand-emphasis dark:hover:border-dark-tremor-brand-emphasis"};case"secondary":return{textColor:t?(0,c.getColorClassNames)(t,g.colorPalette.text).textColor:"text-tremor-brand dark:text-dark-tremor-brand",hoverTextColor:t?(0,c.getColorClassNames)(t,g.colorPalette.text).textColor:"hover:text-tremor-brand-emphasis dark:hover:text-dark-tremor-brand-emphasis",bgColor:(0,c.getColorClassNames)("transparent").bgColor,hoverBgColor:t?(0,d.tremorTwMerge)((0,c.getColorClassNames)(t,g.colorPalette.background).hoverBgColor,"hover:bg-opacity-20 dark:hover:bg-opacity-20"):"hover:bg-tremor-brand-faint dark:hover:bg-dark-tremor-brand-faint",borderColor:t?(0,c.getColorClassNames)(t,g.colorPalette.border).borderColor:"border-tremor-brand dark:border-dark-tremor-brand"};case"light":return{textColor:t?(0,c.getColorClassNames)(t,g.colorPalette.text).textColor:"text-tremor-brand dark:text-dark-tremor-brand",hoverTextColor:t?(0,c.getColorClassNames)(t,g.colorPalette.darkText).hoverTextColor:"hover:text-tremor-brand-emphasis dark:hover:text-dark-tremor-brand-emphasis",bgColor:(0,c.getColorClassNames)("transparent").bgColor,borderColor:"",hoverBorderColor:""}}},h=(0,c.makeClassName)("Button"),b=({loading:e,iconSize:t,iconPosition:o,Icon:a,needMargin:i,transitionStatus:n})=>{let l=i?o===s.HorizontalPositions.Left?(0,d.tremorTwMerge)("-ml-1","mr-1.5"):(0,d.tremorTwMerge)("-mr-1","ml-1.5"):"",c=(0,d.tremorTwMerge)("w-0 h-0"),g={default:c,entering:c,entered:t,exiting:t,exited:c};return e?r.default.createElement(p,{className:(0,d.tremorTwMerge)(h("icon"),"animate-spin shrink-0",l,g.default,g[n]),style:{transition:"width 150ms"}}):r.default.createElement(a,{className:(0,d.tremorTwMerge)(h("icon"),"shrink-0",t,l)})},f=r.default.forwardRef((e,a)=>{let{icon:p,iconPosition:g=s.HorizontalPositions.Left,size:f=s.Sizes.SM,color:k,variant:_="primary",disabled:v,loading:x=!1,loadingText:w,children:C,tooltip:y,className:$}=e,S=(0,t.__rest)(e,["icon","iconPosition","size","color","variant","disabled","loading","loadingText","children","tooltip","className"]),E=x||v,N=void 0!==p||x,O=x&&w,j=!(!C&&!O),z=(0,d.tremorTwMerge)(m[f].height,m[f].width),I="light"!==_?(0,d.tremorTwMerge)("rounded-tremor-default border","shadow-tremor-input","dark:shadow-dark-tremor-input"):"",T=u(_,k),M=("light"!==_?{xs:{paddingX:"px-2.5",paddingY:"py-1.5",fontSize:"text-xs"},sm:{paddingX:"px-4",paddingY:"py-2",fontSize:"text-sm"},md:{paddingX:"px-4",paddingY:"py-2",fontSize:"text-md"},lg:{paddingX:"px-4",paddingY:"py-2.5",fontSize:"text-lg"},xl:{paddingX:"px-4",paddingY:"py-3",fontSize:"text-xl"}}:{xs:{paddingX:"",paddingY:"",fontSize:"text-xs"},sm:{paddingX:"",paddingY:"",fontSize:"text-sm"},md:{paddingX:"",paddingY:"",fontSize:"text-md"},lg:{paddingX:"",paddingY:"",fontSize:"text-lg"},xl:{paddingX:"",paddingY:"",fontSize:"text-xl"}})[f],{tooltipProps:R,getReferenceProps:P}=(0,o.useTooltip)(300),[A,q]=(({enter:e=!0,exit:t=!0,preEnter:o,preExit:a,timeout:s,initialEntered:d,mountOnEnter:c,unmountOnExit:p,onStateChange:g}={})=>{let[m,u]=(0,r.useState)(()=>i(d?2:n(c))),h=(0,r.useRef)(m),b=(0,r.useRef)(0),[f,k]="object"==typeof s?[s.enter,s.exit]:[s,s],_=(0,r.useCallback)(()=>{let e=((e,t)=>{switch(e){case 1:case 0:return 2;case 4:case 3:return n(t)}})(h.current._s,p);e&&l(e,u,h,b,g)},[g,p]);return[m,(0,r.useCallback)(r=>{let i=e=>{switch(l(e,u,h,b,g),e){case 1:f>=0&&(b.current=((...e)=>setTimeout(...e))(_,f));break;case 4:k>=0&&(b.current=((...e)=>setTimeout(...e))(_,k));break;case 0:case 3:b.current=((...e)=>setTimeout(...e))(()=>{isNaN(document.body.offsetTop)||i(e+1)},0)}},s=h.current.isEnter;"boolean"!=typeof r&&(r=!s),r?s||i(e?+!o:2):s&&i(t?a?3:4:n(p))},[_,g,e,t,o,a,f,k,p]),_]})({timeout:50});return(0,r.useEffect)(()=>{q(x)},[x]),r.default.createElement("button",Object.assign({ref:(0,c.mergeRefs)([a,R.refs.setReference]),className:(0,d.tremorTwMerge)(h("root"),"shrink-0 inline-flex justify-center items-center group font-medium outline-none",I,M.paddingX,M.paddingY,M.fontSize,T.textColor,T.bgColor,T.borderColor,T.hoverBorderColor,E?"opacity-50 cursor-not-allowed":(0,d.tremorTwMerge)(u(_,k).hoverTextColor,u(_,k).hoverBgColor,u(_,k).hoverBorderColor),$),disabled:E},P,S),r.default.createElement(o.default,Object.assign({text:y},R)),N&&g!==s.HorizontalPositions.Right?r.default.createElement(b,{loading:x,iconSize:z,iconPosition:g,Icon:p,transitionStatus:A.status,needMargin:j}):null,O||C?r.default.createElement("span",{className:(0,d.tremorTwMerge)(h("text"),"text-tremor-default whitespace-nowrap")},O?w:C):null,N&&g===s.HorizontalPositions.Right?r.default.createElement(b,{loading:x,iconSize:z,iconPosition:g,Icon:p,transitionStatus:A.status,needMargin:j}):null)});f.displayName="Button",e.s(["Button",0,f],994388)},190272,785913,e=>{"use strict";var t,o,r=((t={}).AUDIO_SPEECH="audio_speech",t.AUDIO_TRANSCRIPTION="audio_transcription",t.IMAGE_GENERATION="image_generation",t.VIDEO_GENERATION="video_generation",t.CHAT="chat",t.RESPONSES="responses",t.IMAGE_EDITS="image_edits",t.ANTHROPIC_MESSAGES="anthropic_messages",t.EMBEDDING="embedding",t),a=((o={}).IMAGE="image",o.VIDEO="video",o.CHAT="chat",o.RESPONSES="responses",o.IMAGE_EDITS="image_edits",o.ANTHROPIC_MESSAGES="anthropic_messages",o.EMBEDDINGS="embeddings",o.SPEECH="speech",o.TRANSCRIPTION="transcription",o.A2A_AGENTS="a2a_agents",o.MCP="mcp",o.REALTIME="realtime",o.INTERACTIONS="interactions",o);let i={image_generation:"image",video_generation:"video",chat:"chat",responses:"responses",image_edits:"image_edits",anthropic_messages:"anthropic_messages",audio_speech:"speech",audio_transcription:"transcription",embedding:"embeddings"};e.s(["EndpointType",()=>a,"getEndpointType",0,e=>{if(console.log("getEndpointType:",e),Object.values(r).includes(e)){let t=i[e];return console.log("endpointType:",t),t}return"chat"}],785913),e.s(["generateCodeSnippet",0,e=>{let t,{apiKeySource:o,accessToken:r,apiKey:i,inputMessage:n,chatHistory:l,selectedTags:s,selectedVectorStores:d,selectedGuardrails:c,selectedPolicies:p,selectedMCPServers:g,mcpServers:m,mcpServerToolRestrictions:u,selectedVoice:h,endpointType:b,selectedModel:f,selectedSdk:k,proxySettings:_}=e,v="session"===o?r:i,x=window.location.origin,w=_?.LITELLM_UI_API_DOC_BASE_URL;w&&w.trim()?x=w:_?.PROXY_BASE_URL&&(x=_.PROXY_BASE_URL);let C=n||"Your prompt here",y=C.replace(/\\/g,"\\\\").replace(/"/g,'\\"').replace(/\n/g,"\\n"),$=l.filter(e=>!e.isImage).map(({role:e,content:t})=>({role:e,content:t})),S={};s.length>0&&(S.tags=s),d.length>0&&(S.vector_stores=d),c.length>0&&(S.guardrails=c),p.length>0&&(S.policies=p);let E=f||"your-model-name",N="azure"===k?`import openai

client = openai.AzureOpenAI(
	api_key="${v||"YOUR_LITELLM_API_KEY"}",
	azure_endpoint="${x}",
	api_version="2024-02-01"
)`:`import openai

client = openai.OpenAI(
	api_key="${v||"YOUR_LITELLM_API_KEY"}",
	base_url="${x}"
)`;switch(b){case a.CHAT:{let e=Object.keys(S).length>0,o="";if(e){let e=JSON.stringify({metadata:S},null,2).split("\n").map(e=>" ".repeat(4)+e).join("\n").trim();o=`,
    extra_body=${e}`}let r=$.length>0?$:[{role:"user",content:C}];t=`
import base64

# Helper function to encode images to base64
def encode_image(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

# Example with text only
response = client.chat.completions.create(
    model="${E}",
    messages=${JSON.stringify(r,null,4)}${o}
)

print(response)

# Example with image or PDF (uncomment and provide file path to use)
# base64_file = encode_image("path/to/your/file.jpg")  # or .pdf
# response_with_file = client.chat.completions.create(
#     model="${E}",
#     messages=[
#         {
#             "role": "user",
#             "content": [
#                 {
#                     "type": "text",
#                     "text": "${y}"
#                 },
#                 {
#                     "type": "image_url",
#                     "image_url": {
#                         "url": f"data:image/jpeg;base64,{base64_file}"  # or data:application/pdf;base64,{base64_file}
#                     }
#                 }
#             ]
#         }
#     ]${o}
# )
# print(response_with_file)
`;break}case a.RESPONSES:{let e=Object.keys(S).length>0,o="";if(e){let e=JSON.stringify({metadata:S},null,2).split("\n").map(e=>" ".repeat(4)+e).join("\n").trim();o=`,
    extra_body=${e}`}let r=$.length>0?$:[{role:"user",content:C}];t=`
import base64

# Helper function to encode images to base64
def encode_image(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

# Example with text only
response = client.responses.create(
    model="${E}",
    input=${JSON.stringify(r,null,4)}${o}
)

print(response.output_text)

# Example with image or PDF (uncomment and provide file path to use)
# base64_file = encode_image("path/to/your/file.jpg")  # or .pdf
# response_with_file = client.responses.create(
#     model="${E}",
#     input=[
#         {
#             "role": "user",
#             "content": [
#                 {"type": "input_text", "text": "${y}"},
#                 {
#                     "type": "input_image",
#                     "image_url": f"data:image/jpeg;base64,{base64_file}",  # or data:application/pdf;base64,{base64_file}
#                 },
#             ],
#         }
#     ]${o}
# )
# print(response_with_file.output_text)
`;break}case a.IMAGE:t="azure"===k?`
# NOTE: The Azure SDK does not have a direct equivalent to the multi-modal 'responses.create' method shown for OpenAI.
# This snippet uses 'client.images.generate' and will create a new image based on your prompt.
# It does not use the uploaded image, as 'client.images.generate' does not support image inputs in this context.
import os
import requests
import json
import time
from PIL import Image

result = client.images.generate(
	model="${E}",
	prompt="${n}",
	n=1
)

json_response = json.loads(result.model_dump_json())

# Set the directory for the stored image
image_dir = os.path.join(os.curdir, 'images')

# If the directory doesn't exist, create it
if not os.path.isdir(image_dir):
	os.mkdir(image_dir)

# Initialize the image path
image_filename = f"generated_image_{int(time.time())}.png"
image_path = os.path.join(image_dir, image_filename)

try:
	# Retrieve the generated image
	if json_response.get("data") && len(json_response["data"]) > 0 && json_response["data"][0].get("url"):
			image_url = json_response["data"][0]["url"]
			generated_image = requests.get(image_url).content
			with open(image_path, "wb") as image_file:
					image_file.write(generated_image)

			print(f"Image saved to {image_path}")
			# Display the image
			image = Image.open(image_path)
			image.show()
	else:
			print("Could not find image URL in response.")
			print("Full response:", json_response)
except Exception as e:
	print(f"An error occurred: {e}")
	print("Full response:", json_response)
`:`
import base64
import os
import time
import json
from PIL import Image
import requests

# Helper function to encode images to base64
def encode_image(image_path):
	with open(image_path, "rb") as image_file:
			return base64.b64encode(image_file.read()).decode('utf-8')

# Helper function to create a file (simplified for this example)
def create_file(image_path):
	# In a real implementation, this would upload the file to OpenAI
	# For this example, we'll just return a placeholder ID
	return f"file_{os.path.basename(image_path).replace('.', '_')}"

# The prompt entered by the user
prompt = "${y}"

# Encode images to base64
base64_image1 = encode_image("body-lotion.png")
base64_image2 = encode_image("soap.png")

# Create file IDs
file_id1 = create_file("body-lotion.png")
file_id2 = create_file("incense-kit.png")

response = client.responses.create(
	model="${E}",
	input=[
			{
					"role": "user",
					"content": [
							{"type": "input_text", "text": prompt},
							{
									"type": "input_image",
									"image_url": f"data:image/jpeg;base64,{base64_image1}",
							},
							{
									"type": "input_image",
									"image_url": f"data:image/jpeg;base64,{base64_image2}",
							},
							{
									"type": "input_image",
									"file_id": file_id1,
							},
							{
									"type": "input_image",
									"file_id": file_id2,
							}
					],
			}
	],
	tools=[{"type": "image_generation"}],
)

# Process the response
image_generation_calls = [
	output
	for output in response.output
	if output.type == "image_generation_call"
]

image_data = [output.result for output in image_generation_calls]

if image_data:
	image_base64 = image_data[0]
	image_filename = f"edited_image_{int(time.time())}.png"
	with open(image_filename, "wb") as f:
			f.write(base64.b64decode(image_base64))
	print(f"Image saved to {image_filename}")
else:
	# If no image is generated, there might be a text response with an explanation
	text_response = [output.text for output in response.output if hasattr(output, 'text')]
	if text_response:
			print("No image generated. Model response:")
			print("\\n".join(text_response))
	else:
			print("No image data found in response.")
	print("Full response for debugging:")
	print(response)
`;break;case a.IMAGE_EDITS:t="azure"===k?`
import base64
import os
import time
import json
from PIL import Image
import requests

# Helper function to encode images to base64
def encode_image(image_path):
	with open(image_path, "rb") as image_file:
			return base64.b64encode(image_file.read()).decode('utf-8')

# The prompt entered by the user
prompt = "${y}"

# Encode images to base64
base64_image1 = encode_image("body-lotion.png")
base64_image2 = encode_image("soap.png")

# Create file IDs
file_id1 = create_file("body-lotion.png")
file_id2 = create_file("incense-kit.png")

response = client.responses.create(
	model="${E}",
	input=[
			{
					"role": "user",
					"content": [
							{"type": "input_text", "text": prompt},
							{
									"type": "input_image",
									"image_url": f"data:image/jpeg;base64,{base64_image1}",
							},
							{
									"type": "input_image",
									"image_url": f"data:image/jpeg;base64,{base64_image2}",
							},
							{
									"type": "input_image",
									"file_id": file_id1,
							},
							{
									"type": "input_image",
									"file_id": file_id2,
							}
					],
			}
	],
	tools=[{"type": "image_generation"}],
)

# Process the response
image_generation_calls = [
	output
	for output in response.output
	if output.type == "image_generation_call"
]

image_data = [output.result for output in image_generation_calls]

if image_data:
	image_base64 = image_data[0]
	image_filename = f"edited_image_{int(time.time())}.png"
	with open(image_filename, "wb") as f:
			f.write(base64.b64decode(image_base64))
	print(f"Image saved to {image_filename}")
else:
	# If no image is generated, there might be a text response with an explanation
	text_response = [output.text for output in response.output if hasattr(output, 'text')]
	if text_response:
			print("No image generated. Model response:")
			print("\\n".join(text_response))
	else:
			print("No image data found in response.")
	print("Full response for debugging:")
	print(response)
`:`
import base64
import os
import time

# Helper function to encode images to base64
def encode_image(image_path):
	with open(image_path, "rb") as image_file:
			return base64.b64encode(image_file.read()).decode('utf-8')

# Helper function to create a file (simplified for this example)
def create_file(image_path):
	# In a real implementation, this would upload the file to OpenAI
	# For this example, we'll just return a placeholder ID
	return f"file_{os.path.basename(image_path).replace('.', '_')}"

# The prompt entered by the user
prompt = "${y}"

# Encode images to base64
base64_image1 = encode_image("body-lotion.png")
base64_image2 = encode_image("soap.png")

# Create file IDs
file_id1 = create_file("body-lotion.png")
file_id2 = create_file("incense-kit.png")

response = client.responses.create(
	model="${E}",
	input=[
			{
					"role": "user",
					"content": [
							{"type": "input_text", "text": prompt},
							{
									"type": "input_image",
									"image_url": f"data:image/jpeg;base64,{base64_image1}",
							},
							{
									"type": "input_image",
									"image_url": f"data:image/jpeg;base64,{base64_image2}",
							},
							{
									"type": "input_image",
									"file_id": file_id1,
							},
							{
									"type": "input_image",
									"file_id": file_id2,
							}
					],
			}
	],
	tools=[{"type": "image_generation"}],
)

# Process the response
image_generation_calls = [
	output
	for output in response.output
	if output.type == "image_generation_call"
]

image_data = [output.result for output in image_generation_calls]

if image_data:
	image_base64 = image_data[0]
	image_filename = f"edited_image_{int(time.time())}.png"
	with open(image_filename, "wb") as f:
			f.write(base64.b64decode(image_base64))
	print(f"Image saved to {image_filename}")
else:
	# If no image is generated, there might be a text response with an explanation
	text_response = [output.text for output in response.output if hasattr(output, 'text')]
	if text_response:
			print("No image generated. Model response:")
			print("\\n".join(text_response))
	else:
			print("No image data found in response.")
	print("Full response for debugging:")
	print(response)
`;break;case a.EMBEDDINGS:t=`
response = client.embeddings.create(
	input="${n||"Your string here"}",
	model="${E}",
	encoding_format="base64" # or "float"
)

print(response.data[0].embedding)
`;break;case a.TRANSCRIPTION:t=`
# Open the audio file
audio_file = open("path/to/your/audio/file.mp3", "rb")

# Make the transcription request
response = client.audio.transcriptions.create(
	model="${E}",
	file=audio_file${n?`,
	prompt="${n.replace(/\\/g,"\\\\").replace(/"/g,'\\"')}"`:""}
)

print(response.text)
`;break;case a.SPEECH:t=`
# Make the text-to-speech request
response = client.audio.speech.create(
	model="${E}",
	input="${n||"Your text to convert to speech here"}",
	voice="${h}"  # Options: alloy, ash, ballad, coral, echo, fable, nova, onyx, sage, shimmer
)

# Save the audio to a file
output_filename = "output_speech.mp3"
response.stream_to_file(output_filename)
print(f"Audio saved to {output_filename}")

# Optional: Customize response format and speed
# response = client.audio.speech.create(
#     model="${E}",
#     input="${n||"Your text to convert to speech here"}",
#     voice="alloy",
#     response_format="mp3",  # Options: mp3, opus, aac, flac, wav, pcm
#     speed=1.0  # Range: 0.25 to 4.0
# )
# response.stream_to_file("output_speech.mp3")
`;break;default:t="\n# Code generation for this endpoint is not implemented yet."}return`${N}
${t}`}],190272)},673709,678745,678784,e=>{"use strict";var t=e.i(843476),o=e.i(271645),r=e.i(475254);let a=(0,r.default)("check",[["path",{d:"M20 6 9 17l-5-5",key:"1gmf2c"}]]);e.s(["default",0,a],678745),e.s(["CheckIcon",0,a],678784);let i=(0,r.default)("clipboard",[["rect",{width:"8",height:"4",x:"8",y:"2",rx:"1",ry:"1",key:"tgr4d6"}],["path",{d:"M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2",key:"116196"}]]);var n=e.i(650056);let l={'code[class*="language-"]':{background:"hsl(230, 1%, 98%)",color:"hsl(230, 8%, 24%)",fontFamily:'"Fira Code", "Fira Mono", Menlo, Consolas, "DejaVu Sans Mono", monospace',direction:"ltr",textAlign:"left",whiteSpace:"pre",wordSpacing:"normal",wordBreak:"normal",lineHeight:"1.5",MozTabSize:"2",OTabSize:"2",tabSize:"2",WebkitHyphens:"none",MozHyphens:"none",msHyphens:"none",hyphens:"none"},'pre[class*="language-"]':{background:"hsl(230, 1%, 98%)",color:"hsl(230, 8%, 24%)",fontFamily:'"Fira Code", "Fira Mono", Menlo, Consolas, "DejaVu Sans Mono", monospace',direction:"ltr",textAlign:"left",whiteSpace:"pre",wordSpacing:"normal",wordBreak:"normal",lineHeight:"1.5",MozTabSize:"2",OTabSize:"2",tabSize:"2",WebkitHyphens:"none",MozHyphens:"none",msHyphens:"none",hyphens:"none",padding:"1em",margin:"0.5em 0",overflow:"auto",borderRadius:"0.3em"},'code[class*="language-"]::-moz-selection':{background:"hsl(230, 1%, 90%)",color:"inherit"},'code[class*="language-"] *::-moz-selection':{background:"hsl(230, 1%, 90%)",color:"inherit"},'pre[class*="language-"] *::-moz-selection':{background:"hsl(230, 1%, 90%)",color:"inherit"},'code[class*="language-"]::selection':{background:"hsl(230, 1%, 90%)",color:"inherit"},'code[class*="language-"] *::selection':{background:"hsl(230, 1%, 90%)",color:"inherit"},'pre[class*="language-"] *::selection':{background:"hsl(230, 1%, 90%)",color:"inherit"},':not(pre) > code[class*="language-"]':{padding:"0.2em 0.3em",borderRadius:"0.3em",whiteSpace:"normal"},comment:{color:"hsl(230, 4%, 64%)",fontStyle:"italic"},prolog:{color:"hsl(230, 4%, 64%)"},cdata:{color:"hsl(230, 4%, 64%)"},doctype:{color:"hsl(230, 8%, 24%)"},punctuation:{color:"hsl(230, 8%, 24%)"},entity:{color:"hsl(230, 8%, 24%)",cursor:"help"},"attr-name":{color:"hsl(35, 99%, 36%)"},"class-name":{color:"hsl(35, 99%, 36%)"},boolean:{color:"hsl(35, 99%, 36%)"},constant:{color:"hsl(35, 99%, 36%)"},number:{color:"hsl(35, 99%, 36%)"},atrule:{color:"hsl(35, 99%, 36%)"},keyword:{color:"hsl(301, 63%, 40%)"},property:{color:"hsl(5, 74%, 59%)"},tag:{color:"hsl(5, 74%, 59%)"},symbol:{color:"hsl(5, 74%, 59%)"},deleted:{color:"hsl(5, 74%, 59%)"},important:{color:"hsl(5, 74%, 59%)"},selector:{color:"hsl(119, 34%, 47%)"},string:{color:"hsl(119, 34%, 47%)"},char:{color:"hsl(119, 34%, 47%)"},builtin:{color:"hsl(119, 34%, 47%)"},inserted:{color:"hsl(119, 34%, 47%)"},regex:{color:"hsl(119, 34%, 47%)"},"attr-value":{color:"hsl(119, 34%, 47%)"},"attr-value > .token.punctuation":{color:"hsl(119, 34%, 47%)"},variable:{color:"hsl(221, 87%, 60%)"},operator:{color:"hsl(221, 87%, 60%)"},function:{color:"hsl(221, 87%, 60%)"},url:{color:"hsl(198, 99%, 37%)"},"attr-value > .token.punctuation.attr-equals":{color:"hsl(230, 8%, 24%)"},"special-attr > .token.attr-value > .token.value.css":{color:"hsl(230, 8%, 24%)"},".language-css .token.selector":{color:"hsl(5, 74%, 59%)"},".language-css .token.property":{color:"hsl(230, 8%, 24%)"},".language-css .token.function":{color:"hsl(198, 99%, 37%)"},".language-css .token.url > .token.function":{color:"hsl(198, 99%, 37%)"},".language-css .token.url > .token.string.url":{color:"hsl(119, 34%, 47%)"},".language-css .token.important":{color:"hsl(301, 63%, 40%)"},".language-css .token.atrule .token.rule":{color:"hsl(301, 63%, 40%)"},".language-javascript .token.operator":{color:"hsl(301, 63%, 40%)"},".language-javascript .token.template-string > .token.interpolation > .token.interpolation-punctuation.punctuation":{color:"hsl(344, 84%, 43%)"},".language-json .token.operator":{color:"hsl(230, 8%, 24%)"},".language-json .token.null.keyword":{color:"hsl(35, 99%, 36%)"},".language-markdown .token.url":{color:"hsl(230, 8%, 24%)"},".language-markdown .token.url > .token.operator":{color:"hsl(230, 8%, 24%)"},".language-markdown .token.url-reference.url > .token.string":{color:"hsl(230, 8%, 24%)"},".language-markdown .token.url > .token.content":{color:"hsl(221, 87%, 60%)"},".language-markdown .token.url > .token.url":{color:"hsl(198, 99%, 37%)"},".language-markdown .token.url-reference.url":{color:"hsl(198, 99%, 37%)"},".language-markdown .token.blockquote.punctuation":{color:"hsl(230, 4%, 64%)",fontStyle:"italic"},".language-markdown .token.hr.punctuation":{color:"hsl(230, 4%, 64%)",fontStyle:"italic"},".language-markdown .token.code-snippet":{color:"hsl(119, 34%, 47%)"},".language-markdown .token.bold .token.content":{color:"hsl(35, 99%, 36%)"},".language-markdown .token.italic .token.content":{color:"hsl(301, 63%, 40%)"},".language-markdown .token.strike .token.content":{color:"hsl(5, 74%, 59%)"},".language-markdown .token.strike .token.punctuation":{color:"hsl(5, 74%, 59%)"},".language-markdown .token.list.punctuation":{color:"hsl(5, 74%, 59%)"},".language-markdown .token.title.important > .token.punctuation":{color:"hsl(5, 74%, 59%)"},bold:{fontWeight:"bold"},italic:{fontStyle:"italic"},namespace:{Opacity:"0.8"},"token.tab:not(:empty):before":{color:"hsla(230, 8%, 24%, 0.2)"},"token.cr:before":{color:"hsla(230, 8%, 24%, 0.2)"},"token.lf:before":{color:"hsla(230, 8%, 24%, 0.2)"},"token.space:before":{color:"hsla(230, 8%, 24%, 0.2)"},"div.code-toolbar > .toolbar.toolbar > .toolbar-item":{marginRight:"0.4em"},"div.code-toolbar > .toolbar.toolbar > .toolbar-item > button":{background:"hsl(230, 1%, 90%)",color:"hsl(230, 6%, 44%)",padding:"0.1em 0.4em",borderRadius:"0.3em"},"div.code-toolbar > .toolbar.toolbar > .toolbar-item > a":{background:"hsl(230, 1%, 90%)",color:"hsl(230, 6%, 44%)",padding:"0.1em 0.4em",borderRadius:"0.3em"},"div.code-toolbar > .toolbar.toolbar > .toolbar-item > span":{background:"hsl(230, 1%, 90%)",color:"hsl(230, 6%, 44%)",padding:"0.1em 0.4em",borderRadius:"0.3em"},"div.code-toolbar > .toolbar.toolbar > .toolbar-item > button:hover":{background:"hsl(230, 1%, 78%)",color:"hsl(230, 8%, 24%)"},"div.code-toolbar > .toolbar.toolbar > .toolbar-item > button:focus":{background:"hsl(230, 1%, 78%)",color:"hsl(230, 8%, 24%)"},"div.code-toolbar > .toolbar.toolbar > .toolbar-item > a:hover":{background:"hsl(230, 1%, 78%)",color:"hsl(230, 8%, 24%)"},"div.code-toolbar > .toolbar.toolbar > .toolbar-item > a:focus":{background:"hsl(230, 1%, 78%)",color:"hsl(230, 8%, 24%)"},"div.code-toolbar > .toolbar.toolbar > .toolbar-item > span:hover":{background:"hsl(230, 1%, 78%)",color:"hsl(230, 8%, 24%)"},"div.code-toolbar > .toolbar.toolbar > .toolbar-item > span:focus":{background:"hsl(230, 1%, 78%)",color:"hsl(230, 8%, 24%)"},".line-highlight.line-highlight":{background:"hsla(230, 8%, 24%, 0.05)"},".line-highlight.line-highlight:before":{background:"hsl(230, 1%, 90%)",color:"hsl(230, 8%, 24%)",padding:"0.1em 0.6em",borderRadius:"0.3em",boxShadow:"0 2px 0 0 rgba(0, 0, 0, 0.2)"},".line-highlight.line-highlight[data-end]:after":{background:"hsl(230, 1%, 90%)",color:"hsl(230, 8%, 24%)",padding:"0.1em 0.6em",borderRadius:"0.3em",boxShadow:"0 2px 0 0 rgba(0, 0, 0, 0.2)"},"pre[id].linkable-line-numbers.linkable-line-numbers span.line-numbers-rows > span:hover:before":{backgroundColor:"hsla(230, 8%, 24%, 0.05)"},".line-numbers.line-numbers .line-numbers-rows":{borderRightColor:"hsla(230, 8%, 24%, 0.2)"},".command-line .command-line-prompt":{borderRightColor:"hsla(230, 8%, 24%, 0.2)"},".line-numbers .line-numbers-rows > span:before":{color:"hsl(230, 1%, 62%)"},".command-line .command-line-prompt > span:before":{color:"hsl(230, 1%, 62%)"},".rainbow-braces .token.token.punctuation.brace-level-1":{color:"hsl(5, 74%, 59%)"},".rainbow-braces .token.token.punctuation.brace-level-5":{color:"hsl(5, 74%, 59%)"},".rainbow-braces .token.token.punctuation.brace-level-9":{color:"hsl(5, 74%, 59%)"},".rainbow-braces .token.token.punctuation.brace-level-2":{color:"hsl(119, 34%, 47%)"},".rainbow-braces .token.token.punctuation.brace-level-6":{color:"hsl(119, 34%, 47%)"},".rainbow-braces .token.token.punctuation.brace-level-10":{color:"hsl(119, 34%, 47%)"},".rainbow-braces .token.token.punctuation.brace-level-3":{color:"hsl(221, 87%, 60%)"},".rainbow-braces .token.token.punctuation.brace-level-7":{color:"hsl(221, 87%, 60%)"},".rainbow-braces .token.token.punctuation.brace-level-11":{color:"hsl(221, 87%, 60%)"},".rainbow-braces .token.token.punctuation.brace-level-4":{color:"hsl(301, 63%, 40%)"},".rainbow-braces .token.token.punctuation.brace-level-8":{color:"hsl(301, 63%, 40%)"},".rainbow-braces .token.token.punctuation.brace-level-12":{color:"hsl(301, 63%, 40%)"},"pre.diff-highlight > code .token.token.deleted:not(.prefix)":{backgroundColor:"hsla(353, 100%, 66%, 0.15)"},"pre > code.diff-highlight .token.token.deleted:not(.prefix)":{backgroundColor:"hsla(353, 100%, 66%, 0.15)"},"pre.diff-highlight > code .token.token.deleted:not(.prefix)::-moz-selection":{backgroundColor:"hsla(353, 95%, 66%, 0.25)"},"pre.diff-highlight > code .token.token.deleted:not(.prefix) *::-moz-selection":{backgroundColor:"hsla(353, 95%, 66%, 0.25)"},"pre > code.diff-highlight .token.token.deleted:not(.prefix)::-moz-selection":{backgroundColor:"hsla(353, 95%, 66%, 0.25)"},"pre > code.diff-highlight .token.token.deleted:not(.prefix) *::-moz-selection":{backgroundColor:"hsla(353, 95%, 66%, 0.25)"},"pre.diff-highlight > code .token.token.deleted:not(.prefix)::selection":{backgroundColor:"hsla(353, 95%, 66%, 0.25)"},"pre.diff-highlight > code .token.token.deleted:not(.prefix) *::selection":{backgroundColor:"hsla(353, 95%, 66%, 0.25)"},"pre > code.diff-highlight .token.token.deleted:not(.prefix)::selection":{backgroundColor:"hsla(353, 95%, 66%, 0.25)"},"pre > code.diff-highlight .token.token.deleted:not(.prefix) *::selection":{backgroundColor:"hsla(353, 95%, 66%, 0.25)"},"pre.diff-highlight > code .token.token.inserted:not(.prefix)":{backgroundColor:"hsla(137, 100%, 55%, 0.15)"},"pre > code.diff-highlight .token.token.inserted:not(.prefix)":{backgroundColor:"hsla(137, 100%, 55%, 0.15)"},"pre.diff-highlight > code .token.token.inserted:not(.prefix)::-moz-selection":{backgroundColor:"hsla(135, 73%, 55%, 0.25)"},"pre.diff-highlight > code .token.token.inserted:not(.prefix) *::-moz-selection":{backgroundColor:"hsla(135, 73%, 55%, 0.25)"},"pre > code.diff-highlight .token.token.inserted:not(.prefix)::-moz-selection":{backgroundColor:"hsla(135, 73%, 55%, 0.25)"},"pre > code.diff-highlight .token.token.inserted:not(.prefix) *::-moz-selection":{backgroundColor:"hsla(135, 73%, 55%, 0.25)"},"pre.diff-highlight > code .token.token.inserted:not(.prefix)::selection":{backgroundColor:"hsla(135, 73%, 55%, 0.25)"},"pre.diff-highlight > code .token.token.inserted:not(.prefix) *::selection":{backgroundColor:"hsla(135, 73%, 55%, 0.25)"},"pre > code.diff-highlight .token.token.inserted:not(.prefix)::selection":{backgroundColor:"hsla(135, 73%, 55%, 0.25)"},"pre > code.diff-highlight .token.token.inserted:not(.prefix) *::selection":{backgroundColor:"hsla(135, 73%, 55%, 0.25)"},".prism-previewer.prism-previewer:before":{borderColor:"hsl(0, 0, 95%)"},".prism-previewer-gradient.prism-previewer-gradient div":{borderColor:"hsl(0, 0, 95%)",borderRadius:"0.3em"},".prism-previewer-color.prism-previewer-color:before":{borderRadius:"0.3em"},".prism-previewer-easing.prism-previewer-easing:before":{borderRadius:"0.3em"},".prism-previewer.prism-previewer:after":{borderTopColor:"hsl(0, 0, 95%)"},".prism-previewer-flipped.prism-previewer-flipped.after":{borderBottomColor:"hsl(0, 0, 95%)"},".prism-previewer-angle.prism-previewer-angle:before":{background:"hsl(0, 0%, 100%)"},".prism-previewer-time.prism-previewer-time:before":{background:"hsl(0, 0%, 100%)"},".prism-previewer-easing.prism-previewer-easing":{background:"hsl(0, 0%, 100%)"},".prism-previewer-angle.prism-previewer-angle circle":{stroke:"hsl(230, 8%, 24%)",strokeOpacity:"1"},".prism-previewer-time.prism-previewer-time circle":{stroke:"hsl(230, 8%, 24%)",strokeOpacity:"1"},".prism-previewer-easing.prism-previewer-easing circle":{stroke:"hsl(230, 8%, 24%)",fill:"transparent"},".prism-previewer-easing.prism-previewer-easing path":{stroke:"hsl(230, 8%, 24%)"},".prism-previewer-easing.prism-previewer-easing line":{stroke:"hsl(230, 8%, 24%)"}};e.s(["default",0,({code:e,language:r})=>{let[s,d]=(0,o.useState)(!1);return(0,t.jsxs)("div",{className:"relative rounded-lg border border-gray-200 overflow-hidden",children:[(0,t.jsx)("button",{onClick:()=>{navigator.clipboard.writeText(e),d(!0),setTimeout(()=>d(!1),2e3)},className:"absolute top-3 right-3 p-2 rounded-md bg-gray-100 hover:bg-gray-200 text-gray-600 z-10","aria-label":"Copy code",children:s?(0,t.jsx)(a,{size:16}):(0,t.jsx)(i,{size:16})}),(0,t.jsx)(n.Prism,{language:r,style:l,customStyle:{margin:0,padding:"1.5rem",borderRadius:"0.5rem",fontSize:"0.9rem",backgroundColor:"#fafafa"},showLineNumbers:!0,children:e})]})}],673709)},434166,e=>{"use strict";e.s(["getSecureItem",0,function(e){try{let t=window.sessionStorage.getItem(e);if(null===t)return null;return decodeURIComponent(atob(t).split("").map(e=>"%"+e.charCodeAt(0).toString(16).padStart(2,"0")).join(""))}catch{return null}},"setSecureItem",0,function(e,t){window.sessionStorage.setItem(e,btoa(encodeURIComponent(t).replace(/%([0-9A-F]{2})/g,(e,t)=>String.fromCharCode(parseInt(t,16)))))}])},959013,e=>{"use strict";e.i(247167);var t=e.i(931067),o=e.i(271645);let r={icon:{tag:"svg",attrs:{viewBox:"64 64 896 896",focusable:"false"},children:[{tag:"path",attrs:{d:"M482 152h60q8 0 8 8v704q0 8-8 8h-60q-8 0-8-8V160q0-8 8-8z"}},{tag:"path",attrs:{d:"M192 474h672q8 0 8 8v60q0 8-8 8H160q-8 0-8-8v-60q0-8 8-8z"}}]},name:"plus",theme:"outlined"};var a=e.i(9583),i=o.forwardRef(function(e,i){return o.createElement(a.default,(0,t.default)({},e,{ref:i,icon:r}))});e.s(["default",0,i],959013)},185793,e=>{"use strict";e.i(247167);var t=e.i(271645),o=e.i(343794),r=e.i(242064),a=e.i(529681);let i=e=>{let{prefixCls:r,className:a,style:i,size:n,shape:l}=e,s=(0,o.default)({[`${r}-lg`]:"large"===n,[`${r}-sm`]:"small"===n}),d=(0,o.default)({[`${r}-circle`]:"circle"===l,[`${r}-square`]:"square"===l,[`${r}-round`]:"round"===l}),c=t.useMemo(()=>"number"==typeof n?{width:n,height:n,lineHeight:`${n}px`}:{},[n]);return t.createElement("span",{className:(0,o.default)(r,s,d,a),style:Object.assign(Object.assign({},c),i)})};e.i(296059);var n=e.i(694758),l=e.i(915654),s=e.i(246422),d=e.i(838378);let c=new n.Keyframes("ant-skeleton-loading",{"0%":{backgroundPosition:"100% 50%"},"100%":{backgroundPosition:"0 50%"}}),p=e=>({height:e,lineHeight:(0,l.unit)(e)}),g=e=>Object.assign({width:e},p(e)),m=(e,t)=>Object.assign({width:t(e).mul(5).equal(),minWidth:t(e).mul(5).equal()},p(e)),u=e=>Object.assign({width:e},p(e)),h=(e,t,o)=>{let{skeletonButtonCls:r}=e;return{[`${o}${r}-circle`]:{width:t,minWidth:t,borderRadius:"50%"},[`${o}${r}-round`]:{borderRadius:t}}},b=(e,t)=>Object.assign({width:t(e).mul(2).equal(),minWidth:t(e).mul(2).equal()},p(e)),f=(0,s.genStyleHooks)("Skeleton",e=>{let{componentCls:t,calc:o}=e;return(e=>{let{componentCls:t,skeletonAvatarCls:o,skeletonTitleCls:r,skeletonParagraphCls:a,skeletonButtonCls:i,skeletonInputCls:n,skeletonImageCls:l,controlHeight:s,controlHeightLG:d,controlHeightSM:p,gradientFromColor:f,padding:k,marginSM:_,borderRadius:v,titleHeight:x,blockRadius:w,paragraphLiHeight:C,controlHeightXS:y,paragraphMarginTop:$}=e;return{[t]:{display:"table",width:"100%",[`${t}-header`]:{display:"table-cell",paddingInlineEnd:k,verticalAlign:"top",[o]:Object.assign({display:"inline-block",verticalAlign:"top",background:f},g(s)),[`${o}-circle`]:{borderRadius:"50%"},[`${o}-lg`]:Object.assign({},g(d)),[`${o}-sm`]:Object.assign({},g(p))},[`${t}-content`]:{display:"table-cell",width:"100%",verticalAlign:"top",[r]:{width:"100%",height:x,background:f,borderRadius:w,[`+ ${a}`]:{marginBlockStart:p}},[a]:{padding:0,"> li":{width:"100%",height:C,listStyle:"none",background:f,borderRadius:w,"+ li":{marginBlockStart:y}}},[`${a}> li:last-child:not(:first-child):not(:nth-child(2))`]:{width:"61%"}},[`&-round ${t}-content`]:{[`${r}, ${a} > li`]:{borderRadius:v}}},[`${t}-with-avatar ${t}-content`]:{[r]:{marginBlockStart:_,[`+ ${a}`]:{marginBlockStart:$}}},[`${t}${t}-element`]:Object.assign(Object.assign(Object.assign(Object.assign({display:"inline-block",width:"auto"},(e=>{let{borderRadiusSM:t,skeletonButtonCls:o,controlHeight:r,controlHeightLG:a,controlHeightSM:i,gradientFromColor:n,calc:l}=e;return Object.assign(Object.assign(Object.assign(Object.assign(Object.assign({[o]:Object.assign({display:"inline-block",verticalAlign:"top",background:n,borderRadius:t,width:l(r).mul(2).equal(),minWidth:l(r).mul(2).equal()},b(r,l))},h(e,r,o)),{[`${o}-lg`]:Object.assign({},b(a,l))}),h(e,a,`${o}-lg`)),{[`${o}-sm`]:Object.assign({},b(i,l))}),h(e,i,`${o}-sm`))})(e)),(e=>{let{skeletonAvatarCls:t,gradientFromColor:o,controlHeight:r,controlHeightLG:a,controlHeightSM:i}=e;return{[t]:Object.assign({display:"inline-block",verticalAlign:"top",background:o},g(r)),[`${t}${t}-circle`]:{borderRadius:"50%"},[`${t}${t}-lg`]:Object.assign({},g(a)),[`${t}${t}-sm`]:Object.assign({},g(i))}})(e)),(e=>{let{controlHeight:t,borderRadiusSM:o,skeletonInputCls:r,controlHeightLG:a,controlHeightSM:i,gradientFromColor:n,calc:l}=e;return{[r]:Object.assign({display:"inline-block",verticalAlign:"top",background:n,borderRadius:o},m(t,l)),[`${r}-lg`]:Object.assign({},m(a,l)),[`${r}-sm`]:Object.assign({},m(i,l))}})(e)),(e=>{let{skeletonImageCls:t,imageSizeBase:o,gradientFromColor:r,borderRadiusSM:a,calc:i}=e;return{[t]:Object.assign(Object.assign({display:"inline-flex",alignItems:"center",justifyContent:"center",verticalAlign:"middle",background:r,borderRadius:a},u(i(o).mul(2).equal())),{[`${t}-path`]:{fill:"#bfbfbf"},[`${t}-svg`]:Object.assign(Object.assign({},u(o)),{maxWidth:i(o).mul(4).equal(),maxHeight:i(o).mul(4).equal()}),[`${t}-svg${t}-svg-circle`]:{borderRadius:"50%"}}),[`${t}${t}-circle`]:{borderRadius:"50%"}}})(e)),[`${t}${t}-block`]:{width:"100%",[i]:{width:"100%"},[n]:{width:"100%"}},[`${t}${t}-active`]:{[`
        ${r},
        ${a} > li,
        ${o},
        ${i},
        ${n},
        ${l}
      `]:Object.assign({},{background:e.skeletonLoadingBackground,backgroundSize:"400% 100%",animationName:c,animationDuration:e.skeletonLoadingMotionDuration,animationTimingFunction:"ease",animationIterationCount:"infinite"})}}})((0,d.mergeToken)(e,{skeletonAvatarCls:`${t}-avatar`,skeletonTitleCls:`${t}-title`,skeletonParagraphCls:`${t}-paragraph`,skeletonButtonCls:`${t}-button`,skeletonInputCls:`${t}-input`,skeletonImageCls:`${t}-image`,imageSizeBase:o(e.controlHeight).mul(1.5).equal(),borderRadius:100,skeletonLoadingBackground:`linear-gradient(90deg, ${e.gradientFromColor} 25%, ${e.gradientToColor} 37%, ${e.gradientFromColor} 63%)`,skeletonLoadingMotionDuration:"1.4s"}))},e=>{let{colorFillContent:t,colorFill:o}=e;return{color:t,colorGradientEnd:o,gradientFromColor:t,gradientToColor:o,titleHeight:e.controlHeight/2,blockRadius:e.borderRadiusSM,paragraphMarginTop:e.marginLG+e.marginXXS,paragraphLiHeight:e.controlHeight/2}},{deprecatedTokens:[["color","gradientFromColor"],["colorGradientEnd","gradientToColor"]]}),k=e=>{let{prefixCls:r,className:a,style:i,rows:n=0}=e,l=Array.from({length:n}).map((o,r)=>t.createElement("li",{key:r,style:{width:((e,t)=>{let{width:o,rows:r=2}=t;return Array.isArray(o)?o[e]:r-1===e?o:void 0})(r,e)}}));return t.createElement("ul",{className:(0,o.default)(r,a),style:i},l)},_=({prefixCls:e,className:r,width:a,style:i})=>t.createElement("h3",{className:(0,o.default)(e,r),style:Object.assign({width:a},i)});function v(e){return e&&"object"==typeof e?e:{}}let x=e=>{let{prefixCls:a,loading:n,className:l,rootClassName:s,style:d,children:c,avatar:p=!1,title:g=!0,paragraph:m=!0,active:u,round:h}=e,{getPrefixCls:b,direction:x,className:w,style:C}=(0,r.useComponentConfig)("skeleton"),y=b("skeleton",a),[$,S,E]=f(y);if(n||!("loading"in e)){let e,r,a=!!p,n=!!g,c=!!m;if(a){let o=Object.assign(Object.assign({prefixCls:`${y}-avatar`},n&&!c?{size:"large",shape:"square"}:{size:"large",shape:"circle"}),v(p));e=t.createElement("div",{className:`${y}-header`},t.createElement(i,Object.assign({},o)))}if(n||c){let e,o;if(n){let o=Object.assign(Object.assign({prefixCls:`${y}-title`},!a&&c?{width:"38%"}:a&&c?{width:"50%"}:{}),v(g));e=t.createElement(_,Object.assign({},o))}if(c){let e,r=Object.assign(Object.assign({prefixCls:`${y}-paragraph`},(e={},a&&n||(e.width="61%"),!a&&n?e.rows=3:e.rows=2,e)),v(m));o=t.createElement(k,Object.assign({},r))}r=t.createElement("div",{className:`${y}-content`},e,o)}let b=(0,o.default)(y,{[`${y}-with-avatar`]:a,[`${y}-active`]:u,[`${y}-rtl`]:"rtl"===x,[`${y}-round`]:h},w,l,s,S,E);return $(t.createElement("div",{className:b,style:Object.assign(Object.assign({},C),d)},e,r))}return null!=c?c:null};x.Button=e=>{let{prefixCls:n,className:l,rootClassName:s,active:d,block:c=!1,size:p="default"}=e,{getPrefixCls:g}=t.useContext(r.ConfigContext),m=g("skeleton",n),[u,h,b]=f(m),k=(0,a.default)(e,["prefixCls"]),_=(0,o.default)(m,`${m}-element`,{[`${m}-active`]:d,[`${m}-block`]:c},l,s,h,b);return u(t.createElement("div",{className:_},t.createElement(i,Object.assign({prefixCls:`${m}-button`,size:p},k))))},x.Avatar=e=>{let{prefixCls:n,className:l,rootClassName:s,active:d,shape:c="circle",size:p="default"}=e,{getPrefixCls:g}=t.useContext(r.ConfigContext),m=g("skeleton",n),[u,h,b]=f(m),k=(0,a.default)(e,["prefixCls","className"]),_=(0,o.default)(m,`${m}-element`,{[`${m}-active`]:d},l,s,h,b);return u(t.createElement("div",{className:_},t.createElement(i,Object.assign({prefixCls:`${m}-avatar`,shape:c,size:p},k))))},x.Input=e=>{let{prefixCls:n,className:l,rootClassName:s,active:d,block:c,size:p="default"}=e,{getPrefixCls:g}=t.useContext(r.ConfigContext),m=g("skeleton",n),[u,h,b]=f(m),k=(0,a.default)(e,["prefixCls"]),_=(0,o.default)(m,`${m}-element`,{[`${m}-active`]:d,[`${m}-block`]:c},l,s,h,b);return u(t.createElement("div",{className:_},t.createElement(i,Object.assign({prefixCls:`${m}-input`,size:p},k))))},x.Image=e=>{let{prefixCls:a,className:i,rootClassName:n,style:l,active:s}=e,{getPrefixCls:d}=t.useContext(r.ConfigContext),c=d("skeleton",a),[p,g,m]=f(c),u=(0,o.default)(c,`${c}-element`,{[`${c}-active`]:s},i,n,g,m);return p(t.createElement("div",{className:u},t.createElement("div",{className:(0,o.default)(`${c}-image`,i),style:l},t.createElement("svg",{viewBox:"0 0 1098 1024",xmlns:"http://www.w3.org/2000/svg",className:`${c}-image-svg`},t.createElement("title",null,"Image placeholder"),t.createElement("path",{d:"M365.714286 329.142857q0 45.714286-32.036571 77.677714t-77.677714 32.036571-77.677714-32.036571-32.036571-77.677714 32.036571-77.677714 77.677714-32.036571 77.677714 32.036571 32.036571 77.677714zM950.857143 548.571429l0 256-804.571429 0 0-109.714286 182.857143-182.857143 91.428571 91.428571 292.571429-292.571429zM1005.714286 146.285714l-914.285714 0q-7.460571 0-12.873143 5.412571t-5.412571 12.873143l0 694.857143q0 7.460571 5.412571 12.873143t12.873143 5.412571l914.285714 0q7.460571 0 12.873143-5.412571t5.412571-12.873143l0-694.857143q0-7.460571-5.412571-12.873143t-12.873143-5.412571zM1097.142857 164.571429l0 694.857143q0 37.741714-26.843429 64.585143t-64.585143 26.843429l-914.285714 0q-37.741714 0-64.585143-26.843429t-26.843429-64.585143l0-694.857143q0-37.741714 26.843429-64.585143t64.585143-26.843429l914.285714 0q37.741714 0 64.585143 26.843429t26.843429 64.585143z",className:`${c}-image-path`})))))},x.Node=e=>{let{prefixCls:a,className:i,rootClassName:n,style:l,active:s,children:d}=e,{getPrefixCls:c}=t.useContext(r.ConfigContext),p=c("skeleton",a),[g,m,u]=f(p),h=(0,o.default)(p,`${p}-element`,{[`${p}-active`]:s},m,i,n,u);return g(t.createElement("div",{className:h},t.createElement("div",{className:(0,o.default)(`${p}-image`,i),style:l},d)))},e.s(["default",0,x],185793)},482725,244451,e=>{"use strict";let t;e.i(247167);var o=e.i(271645),r=e.i(343794),a=e.i(242064),i=e.i(763731),n=e.i(174428);let l=80*Math.PI,s=e=>{let{dotClassName:t,style:a,hasCircleCls:i}=e;return o.createElement("circle",{className:(0,r.default)(`${t}-circle`,{[`${t}-circle-bg`]:i}),r:40,cx:50,cy:50,strokeWidth:20,style:a})},d=({percent:e,prefixCls:t})=>{let a=`${t}-dot`,i=`${a}-holder`,d=`${i}-hidden`,[c,p]=o.useState(!1);(0,n.default)(()=>{0!==e&&p(!0)},[0!==e]);let g=Math.max(Math.min(e,100),0);if(!c)return null;let m={strokeDashoffset:`${l/4}`,strokeDasharray:`${l*g/100} ${l*(100-g)/100}`};return o.createElement("span",{className:(0,r.default)(i,`${a}-progress`,g<=0&&d)},o.createElement("svg",{viewBox:"0 0 100 100",role:"progressbar","aria-valuemin":0,"aria-valuemax":100,"aria-valuenow":g},o.createElement(s,{dotClassName:a,hasCircleCls:!0}),o.createElement(s,{dotClassName:a,style:m})))};function c(e){let{prefixCls:t,percent:a=0}=e,i=`${t}-dot`,n=`${i}-holder`,l=`${n}-hidden`;return o.createElement(o.Fragment,null,o.createElement("span",{className:(0,r.default)(n,a>0&&l)},o.createElement("span",{className:(0,r.default)(i,`${t}-dot-spin`)},[1,2,3,4].map(e=>o.createElement("i",{className:`${t}-dot-item`,key:e})))),o.createElement(d,{prefixCls:t,percent:a}))}function p(e){var t;let{prefixCls:a,indicator:n,percent:l}=e,s=`${a}-dot`;return n&&o.isValidElement(n)?(0,i.cloneElement)(n,{className:(0,r.default)(null==(t=n.props)?void 0:t.className,s),percent:l}):o.createElement(c,{prefixCls:a,percent:l})}e.i(296059);var g=e.i(694758),m=e.i(183293),u=e.i(246422),h=e.i(838378);let b=new g.Keyframes("antSpinMove",{to:{opacity:1}}),f=new g.Keyframes("antRotate",{to:{transform:"rotate(405deg)"}}),k=(0,u.genStyleHooks)("Spin",e=>(e=>{let{componentCls:t,calc:o}=e;return{[t]:Object.assign(Object.assign({},(0,m.resetComponent)(e)),{position:"absolute",display:"none",color:e.colorPrimary,fontSize:0,textAlign:"center",verticalAlign:"middle",opacity:0,transition:`transform ${e.motionDurationSlow} ${e.motionEaseInOutCirc}`,"&-spinning":{position:"relative",display:"inline-block",opacity:1},[`${t}-text`]:{fontSize:e.fontSize,paddingTop:o(o(e.dotSize).sub(e.fontSize)).div(2).add(2).equal()},"&-fullscreen":{position:"fixed",width:"100vw",height:"100vh",backgroundColor:e.colorBgMask,zIndex:e.zIndexPopupBase,inset:0,display:"flex",alignItems:"center",flexDirection:"column",justifyContent:"center",opacity:0,visibility:"hidden",transition:`all ${e.motionDurationMid}`,"&-show":{opacity:1,visibility:"visible"},[t]:{[`${t}-dot-holder`]:{color:e.colorWhite},[`${t}-text`]:{color:e.colorTextLightSolid}}},"&-nested-loading":{position:"relative",[`> div > ${t}`]:{position:"absolute",top:0,insetInlineStart:0,zIndex:4,display:"block",width:"100%",height:"100%",maxHeight:e.contentHeight,[`${t}-dot`]:{position:"absolute",top:"50%",insetInlineStart:"50%",margin:o(e.dotSize).mul(-1).div(2).equal()},[`${t}-text`]:{position:"absolute",top:"50%",width:"100%",textShadow:`0 1px 2px ${e.colorBgContainer}`},[`&${t}-show-text ${t}-dot`]:{marginTop:o(e.dotSize).div(2).mul(-1).sub(10).equal()},"&-sm":{[`${t}-dot`]:{margin:o(e.dotSizeSM).mul(-1).div(2).equal()},[`${t}-text`]:{paddingTop:o(o(e.dotSizeSM).sub(e.fontSize)).div(2).add(2).equal()},[`&${t}-show-text ${t}-dot`]:{marginTop:o(e.dotSizeSM).div(2).mul(-1).sub(10).equal()}},"&-lg":{[`${t}-dot`]:{margin:o(e.dotSizeLG).mul(-1).div(2).equal()},[`${t}-text`]:{paddingTop:o(o(e.dotSizeLG).sub(e.fontSize)).div(2).add(2).equal()},[`&${t}-show-text ${t}-dot`]:{marginTop:o(e.dotSizeLG).div(2).mul(-1).sub(10).equal()}}},[`${t}-container`]:{position:"relative",transition:`opacity ${e.motionDurationSlow}`,"&::after":{position:"absolute",top:0,insetInlineEnd:0,bottom:0,insetInlineStart:0,zIndex:10,width:"100%",height:"100%",background:e.colorBgContainer,opacity:0,transition:`all ${e.motionDurationSlow}`,content:'""',pointerEvents:"none"}},[`${t}-blur`]:{clear:"both",opacity:.5,userSelect:"none",pointerEvents:"none","&::after":{opacity:.4,pointerEvents:"auto"}}},"&-tip":{color:e.spinDotDefault},[`${t}-dot-holder`]:{width:"1em",height:"1em",fontSize:e.dotSize,display:"inline-block",transition:`transform ${e.motionDurationSlow} ease, opacity ${e.motionDurationSlow} ease`,transformOrigin:"50% 50%",lineHeight:1,color:e.colorPrimary,"&-hidden":{transform:"scale(0.3)",opacity:0}},[`${t}-dot-progress`]:{position:"absolute",inset:0},[`${t}-dot`]:{position:"relative",display:"inline-block",fontSize:e.dotSize,width:"1em",height:"1em","&-item":{position:"absolute",display:"block",width:o(e.dotSize).sub(o(e.marginXXS).div(2)).div(2).equal(),height:o(e.dotSize).sub(o(e.marginXXS).div(2)).div(2).equal(),background:"currentColor",borderRadius:"100%",transform:"scale(0.75)",transformOrigin:"50% 50%",opacity:.3,animationName:b,animationDuration:"1s",animationIterationCount:"infinite",animationTimingFunction:"linear",animationDirection:"alternate","&:nth-child(1)":{top:0,insetInlineStart:0,animationDelay:"0s"},"&:nth-child(2)":{top:0,insetInlineEnd:0,animationDelay:"0.4s"},"&:nth-child(3)":{insetInlineEnd:0,bottom:0,animationDelay:"0.8s"},"&:nth-child(4)":{bottom:0,insetInlineStart:0,animationDelay:"1.2s"}},"&-spin":{transform:"rotate(45deg)",animationName:f,animationDuration:"1.2s",animationIterationCount:"infinite",animationTimingFunction:"linear"},"&-circle":{strokeLinecap:"round",transition:["stroke-dashoffset","stroke-dasharray","stroke","stroke-width","opacity"].map(t=>`${t} ${e.motionDurationSlow} ease`).join(","),fillOpacity:0,stroke:"currentcolor"},"&-circle-bg":{stroke:e.colorFillSecondary}},[`&-sm ${t}-dot`]:{"&, &-holder":{fontSize:e.dotSizeSM}},[`&-sm ${t}-dot-holder`]:{i:{width:o(o(e.dotSizeSM).sub(o(e.marginXXS).div(2))).div(2).equal(),height:o(o(e.dotSizeSM).sub(o(e.marginXXS).div(2))).div(2).equal()}},[`&-lg ${t}-dot`]:{"&, &-holder":{fontSize:e.dotSizeLG}},[`&-lg ${t}-dot-holder`]:{i:{width:o(o(e.dotSizeLG).sub(e.marginXXS)).div(2).equal(),height:o(o(e.dotSizeLG).sub(e.marginXXS)).div(2).equal()}},[`&${t}-show-text ${t}-text`]:{display:"block"}})}})((0,h.mergeToken)(e,{spinDotDefault:e.colorTextDescription})),e=>{let{controlHeightLG:t,controlHeight:o}=e;return{contentHeight:400,dotSize:t/2,dotSizeSM:.35*t,dotSizeLG:o}}),_=[[30,.05],[70,.03],[96,.01]];var v=function(e,t){var o={};for(var r in e)Object.prototype.hasOwnProperty.call(e,r)&&0>t.indexOf(r)&&(o[r]=e[r]);if(null!=e&&"function"==typeof Object.getOwnPropertySymbols)for(var a=0,r=Object.getOwnPropertySymbols(e);a<r.length;a++)0>t.indexOf(r[a])&&Object.prototype.propertyIsEnumerable.call(e,r[a])&&(o[r[a]]=e[r[a]]);return o};let x=e=>{var i;let{prefixCls:n,spinning:l=!0,delay:s=0,className:d,rootClassName:c,size:g="default",tip:m,wrapperClassName:u,style:h,children:b,fullscreen:f=!1,indicator:x,percent:w}=e,C=v(e,["prefixCls","spinning","delay","className","rootClassName","size","tip","wrapperClassName","style","children","fullscreen","indicator","percent"]),{getPrefixCls:y,direction:$,className:S,style:E,indicator:N}=(0,a.useComponentConfig)("spin"),O=y("spin",n),[j,z,I]=k(O),[T,M]=o.useState(()=>l&&(!l||!s||!!Number.isNaN(Number(s)))),R=function(e,t){let[r,a]=o.useState(0),i=o.useRef(null),n="auto"===t;return o.useEffect(()=>(n&&e&&(a(0),i.current=setInterval(()=>{a(e=>{let t=100-e;for(let o=0;o<_.length;o+=1){let[r,a]=_[o];if(e<=r)return e+t*a}return e})},200)),()=>{i.current&&(clearInterval(i.current),i.current=null)}),[n,e]),n?r:t}(T,w);o.useEffect(()=>{if(l){let e=function(e,t,o){var r,a=o||{},i=a.noTrailing,n=void 0!==i&&i,l=a.noLeading,s=void 0!==l&&l,d=a.debounceMode,c=void 0===d?void 0:d,p=!1,g=0;function m(){r&&clearTimeout(r)}function u(){for(var o=arguments.length,a=Array(o),i=0;i<o;i++)a[i]=arguments[i];var l=this,d=Date.now()-g;function u(){g=Date.now(),t.apply(l,a)}function h(){r=void 0}!p&&(s||!c||r||u(),m(),void 0===c&&d>e?s?(g=Date.now(),n||(r=setTimeout(c?h:u,e))):u():!0!==n&&(r=setTimeout(c?h:u,void 0===c?e-d:e)))}return u.cancel=function(e){var t=(e||{}).upcomingOnly;m(),p=!(void 0!==t&&t)},u}(s,()=>{M(!0)},{debounceMode:false});return e(),()=>{var t;null==(t=null==e?void 0:e.cancel)||t.call(e)}}M(!1)},[s,l]);let P=o.useMemo(()=>void 0!==b&&!f,[b,f]),A=(0,r.default)(O,S,{[`${O}-sm`]:"small"===g,[`${O}-lg`]:"large"===g,[`${O}-spinning`]:T,[`${O}-show-text`]:!!m,[`${O}-rtl`]:"rtl"===$},d,!f&&c,z,I),q=(0,r.default)(`${O}-container`,{[`${O}-blur`]:T}),D=null!=(i=null!=x?x:N)?i:t,H=Object.assign(Object.assign({},E),h),B=o.createElement("div",Object.assign({},C,{style:H,className:A,"aria-live":"polite","aria-busy":T}),o.createElement(p,{prefixCls:O,indicator:D,percent:R}),m&&(P||f)?o.createElement("div",{className:`${O}-text`},m):null);return j(P?o.createElement("div",Object.assign({},C,{className:(0,r.default)(`${O}-nested-loading`,u,z,I)}),T&&o.createElement("div",{key:"loading"},B),o.createElement("div",{className:q,key:"container"},b)):f?o.createElement("div",{className:(0,r.default)(`${O}-fullscreen`,{[`${O}-fullscreen-show`]:T},c,z,I)},B):B)};x.setDefaultIndicator=e=>{t=e},e.s(["default",0,x],244451),e.s(["Spin",0,x],482725)}]);