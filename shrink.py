from PIL import Image

# 1. Open your original uploaded logo
img = Image.open("logo.png")

# 2. Set the target height to 250 pixels
baseheight = 150
hpercent = (baseheight / float(img.size[1]))
wsize = int((float(img.size[0]) * float(hpercent)))

# 3. Use high-quality LANCZOS anti-aliasing to smoothly shrink it
img_small = img.resize((wsize, baseheight), Image.Resampling.LANCZOS)

# 4. Save it over the old file
img_small.save("logo.png")
print(f"Success! Logo cleanly resized to: {img_small.size}")
