# -*- coding: utf-8 -*-
"""
Created on Mon Mar  2 17:40:42 2015

@author: jradavenport, much help by jruan

Try to make a simple one dimensional spectra reduction package

Some ideas from Python4Astronomers tutorial:
    https://python4astronomers.github.io/core/numpy_scipy.html

Plus some simple reduction (flat and bias) methods

Created with the Apache Point Observatory (APO) 3.5-m telescope's
Dual Imaging Spectrograph (DIS) in mind. YMMV

e.g. 
- have BLUE/RED channels
- hand-code in that the RED channel wavelength is backwards
- dispersion along the X, spatial along the Y

+++
Steps to crappy reduction to 1dspec:

1. flat and bias correct (easy)
2. identify lines in wavelength cal image (HeNeAr) and define the
    wavelength solution in 2D space
3. trace the object spectrum, define aperture and sky regions
4. extract object, subtract sky, interpolate wavelength space


---
Things I'm not going to worry about:

- interactive everything (yet)
- cosmic rays
- bad pixels
- overscan
- multiple objects on slit
- extended objects on slit
- flux calibration (yet)

"""

from astropy.io import fits
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.cm as cm
from scipy.optimize import curve_fit
from scipy.interpolate import UnivariateSpline
from scipy.interpolate import SmoothBivariateSpline
import scipy.signal
import os

#########################
def gaus(x,a,b,x0,sigma):
    return a*np.exp(-(x-x0)**2/(2*sigma**2))+b

#########################
def ap_trace(img, nsteps=100):
    '''
    Aperture finding and tracing

    Parameters
    ----------
    img : 2d numpy array, required
        the input image, already trimmed

    Returns
    -------
    1d numpy array, y positions of the trace

    '''
    #--- find the overall max row, and width
    # comp_y = img.sum(axis=1)
    # peak_y = np.argmax(comp_y)
    # popt,pcov = curve_fit(gaus, np.arange(len(comp_y)), comp_y,
    #                       p0=[np.amax(comp_y), np.median(comp_y), peak_y, 2.])
    # peak_dy = popt[3]    

    # median smooth to crudely remove cosmic rays
    img_sm = scipy.signal.medfilt2d(img, kernel_size=(5,5))

    # define the bin edges
    xbins = np.linspace(0, img.shape[1], nsteps)
    ybins = np.zeros_like(xbins)
    for i in range(0,len(xbins)-1):
        #-- simply use the max w/i each window
        #ybins[i] = np.argmax(img_sm[:,xbins[i]:xbins[i+1]].sum(axis=1))

        #-- fit gaussian w/i each window
        zi = img_sm[:,xbins[i]:xbins[i+1]].sum(axis=1)
        yi = np.arange(len(zi))
        popt,pcov = curve_fit(gaus, yi, zi, p0=[np.amax(zi), np.median(zi), np.argmax(zi), 2.])
        ybins[i] = popt[2]

    # recenter the bin positions, trim the unused bin off in Y
    mxbins = (xbins[:-1] + xbins[1:])/2.
    mybins = ybins[:-1]

    # run a cubic spline thru the bins
    ap_spl = UnivariateSpline(mxbins, mybins, ext=0, k=3)

    # interpolate the spline to 1 position per column
    mx = np.arange(0,img.shape[1])
    my = ap_spl(mx)
    return my

#########################
def ap_extract(img, trace, apwidth=5.0):
    # simply add up the total flux around the trace +/- width
    onedspec = np.zeros_like(trace)
    for i in range(0,len(trace)):
        # juuuust in case the trace gets too close to the edge
        # (shouldn't be common)
        widthup = apwidth
        widthdn = apwidth
        if (trace[i]+widthup > img.shape[0]):
            widthup = img.shape[0]-trace[i] - 1
        if (trace[i]-widthdn < 0):
            widthdn = trace[i] - 1

        onedspec[i] = img[trace[i]-widthdn:trace[i]+widthup, i].sum()
    return onedspec


#########################
def sky_fit(img, trace, apwidth=5, skysep=25, skywidth=75, skydeg=2):
    # do simple parabola fit at each pixel
    # (assume wavelength axis is perfectly vertical)
    # return 1-d sky values
    skysubflux = np.zeros_like(trace)
    for i in range(0,len(trace)):
        itrace = int(trace[i])
        y = np.append(np.arange(itrace-skysep-skywidth, itrace-skysep),
                      np.arange(itrace+skysep, itrace+skysep+skywidth))
        z = img[y,i]
        # fit a polynomial to the sky in this column
        pfit = np.polyfit(y,z,skydeg)
        # define the aperture in this column
        ap = np.arange(trace[i]-apwidth,trace[i]+apwidth)
        # evaluate the polynomial across the aperture, and sum
        skysubflux[i] = np.sum(np.polyval(pfit, ap))
    return skysubflux


##########################
def HeNeAr_fit(calimage, linelist='',
               trim=True, fmask=(1,), display=True):
    hdu = fits.open(calimage)
    if trim is False:
        img = hdu[0].data
    if trim is True:
        datasec = hdu[0].header['DATASEC'][1:-1].replace(':',',').split(',')
        d = map(float, datasec)
        img = hdu[0].data[d[2]-1:d[3],d[0]-1:d[1]]

    # this approach will be very DIS specific
    disp_approx = hdu[0].header['DISPDW']
    wcen_approx = hdu[0].header['DISPWC']

    # the red chip wavelength is backwards
    clr = hdu[0].header['DETECTOR']
    if (clr.lower()=='red'):
        sign = -1.0
    else:
        sign = 1.0

    hdu.close(closed=True)

    # take a slice thru the data (+/- 10 pixels) in center row of chip
    slice = img[img.shape[0]/2-10:img.shape[0]/2+10,:].sum(axis=0)

    # use the header info to do rough solution (linear guess)
    wtemp = (np.arange(len(slice))-len(slice)/2) * disp_approx * sign + wcen_approx

    # sort data, cut top 5% of flux data as peak threshold
    tmp = slice.copy()
    tmp.sort()
    flux_thresh = tmp[int(len(tmp)*0.97)]

    # find flux above threshold
    high = np.where( (slice >= flux_thresh) )

    # find  individual peaks (separated by > 1 pixel)
    pk = high[0][ ( (high[0][1:]-high[0][:-1]) > 1 ) ]

    # the number of pixels around the "peak" to fit over
    pwidth = 10
    # offset from start/end of array by at least same # of pixels
    pk = pk[pk > pwidth]
    pk = pk[pk < (len(slice)-pwidth)]

    if display is True:
        plt.figure()
        plt.plot(wtemp, slice, 'b')
        plt.plot(wtemp, np.ones_like(slice)*np.median(slice))
        plt.plot(wtemp, np.ones_like(slice) * flux_thresh)

    pcent_pix = np.zeros_like(pk,dtype='float')
    wcent_pix = np.zeros_like(pk,dtype='float') # wtemp[pk]
    # for each peak, fit a gaussian to find center
    for i in range(len(pk)):
        xi = wtemp[pk[i]-pwidth:pk[i]+pwidth*2]
        yi = slice[pk[i]-pwidth:pk[i]+pwidth*2]

        popt,pcov = curve_fit(gaus, np.arange(len(xi),dtype='float'), yi,
                              p0=(np.amax(yi), np.median(slice), float(np.argmax(yi)), 2.))

        # the gaussian center of the line in pixel units
        pcent_pix[i] = (pk[i]-pwidth) + popt[2]
        # and the peak in wavelength units
        wcent_pix[i] = xi[np.argmax(yi)]

        if display is True:
            plt.scatter(wtemp[pk][i], slice[pk][i], marker='o')
            plt.plot(xi, gaus(np.arange(len(xi)),*popt), 'r')
    if display is True:
        plt.xlabel('approx. wavelength')
        plt.ylabel('flux')
        #plt.show()

    if (len(linelist)==0):
        dir = os.path.dirname(os.path.realpath(__file__))
        linelist = dir + '/resources/dishigh_linelist.txt'

    # import the linelist to match against
    linewave = np.loadtxt(linelist,dtype='float',skiprows=1,usecols=(0,),unpack=True)

    if display is True:
        plt.scatter(linewave,np.ones_like(linewave)*np.max(slice),marker='o',c='blue')
        plt.show()

#   loop thru each peak, from center outwards. a greedy solution
#   find nearest list line. if not line within tolerance, then skip peak
    pcent = []
    wcent = []

    # find center-most lines, sort by dist from center pixels
    ss = np.argsort(np.abs(wcent_pix-wcen_approx))

    tol = 15. # angstroms... might need to make more flexible?
    w1d_order = 3 # the polynomial order for the 1d wavelength solution

    #coeff = [0.0, 0.0, disp_approx*sign, wcen_approx]
    coeff = np.append(np.zeros(w1d_order-1),(disp_approx*sign, wcen_approx))

    for i in range(len(pcent_pix)):
        xx = pcent_pix-len(slice)/2
        #wcent_pix = coeff[3] + xx * coeff[2] + coeff[1] * (xx*xx) + coeff[0] * (xx*xx*xx)
        wcent_pix = np.polyval(coeff, xx)

        if display is True:
            plt.figure()
            plt.plot(wtemp, slice, 'b')
            plt.scatter(linewave,np.ones_like(linewave)*np.max(slice),marker='o',c='cyan')
            plt.scatter(wcent_pix,np.ones_like(wcent_pix)*np.max(slice)/2.,marker='*',c='green')
            plt.scatter(wcent_pix[ss[i]],np.max(slice)/2.,marker='o',c='orange')
        # if there is a match w/i the linear tolerance
        if (min((np.abs(wcent_pix[ss][i] - linewave))) < tol):
            # add corresponding pixel and *actual* wavelength to output vectors
            pcent = np.append(pcent,pcent_pix[ss[i]])
            wcent = np.append(wcent, linewave[np.argmin(np.abs(wcent_pix[ss[i]] - linewave))] )

            if display is True:
                plt.scatter(wcent,np.ones_like(wcent)*np.max(slice),marker='o',c='red')

            if (len(pcent)>w1d_order):
                coeff = np.polyfit(pcent-len(slice)/2,wcent, w1d_order)

        if display is True:
            plt.xlim((min(wtemp),max(wtemp)))
            plt.show()

    # the end result is the vector "coeff" has the wavelength solution for "slice"
    # update the "wtemp" vector that goes with "slice" (fluxes)
    wtemp = np.polyval(coeff, (np.arange(len(slice))-len(slice)/2))

    #-- trace the peaks vertically
    # how far can the trace be bent, i.e. how big a window to fit over?
    maxbend = 10 # pixels (+/-)

    # 3d positions of the peaks: (x,y) and wavelength
    xcent_big = []
    ycent_big = []
    wcent_big = []

    # the valid y-range of the chip
    if (len(fmask)>1):
        ydata = np.arange(img.shape[0])[fmask]
    else:
        ydata = np.arange(img.shape[0])

    # split the chip in to 2 parts, above and below the center
    ydata1 = ydata[np.where((ydata>=img.shape[0]/2))]
    ydata2 = ydata[np.where((ydata<img.shape[0]/2))][::-1]

    img_med = np.median(img)
    # loop over every HeNeAr peak that had a good fit

    for i in range(len(pcent)):
        xline = np.arange(int(pcent[i])-maxbend,int(pcent[i])+maxbend)

        # above center line (where fit was done)
        for j in ydata1:
            yline = img[j, int(pcent[i])-maxbend:int(pcent[i])+maxbend]
            # fit gaussian, assume center at 0, width of 2
            if j==ydata1[0]:
                cguess = pcent[i] # xline[np.argmax(yline)]

            pguess = [np.amax(yline),img_med,cguess,2.]
            popt,pcov = curve_fit(gaus, xline, yline, p0=pguess)
            cguess = popt[2] # update center pixel

            xcent_big = np.append(xcent_big, popt[2])
            ycent_big = np.append(ycent_big, j)
            wcent_big = np.append(wcent_big, wcent[i])
        # below center line, from middle down
        for j in ydata2:
            yline = img[j, int(pcent[i])-maxbend:int(pcent[i])+maxbend]
            # fit gaussian, assume center at 0, width of 2
            if j==ydata1[0]:
                cguess = pcent[i] # xline[np.argmax(yline)]

            pguess = [np.amax(yline),img_med,cguess,2.]
            popt,pcov = curve_fit(gaus, xline, yline, p0=pguess)
            cguess = popt[2] # update center pixel

            xcent_big = np.append(xcent_big, popt[2])
            ycent_big = np.append(ycent_big, j)
            wcent_big = np.append(wcent_big, wcent[i])

    if display is True:
        plt.figure()
        plt.imshow(np.log10(img), origin = 'lower',aspect='auto',cmap=cm.Greys_r)
        plt.colorbar()
        plt.scatter(xcent_big,ycent_big,marker='|',c='r')
        plt.show()

    #-- now the big show!
    #  fit the wavelength solution for the entire chip w/ a 2d spline
    xfitd = 3 # the spline dimension in the wavelength space
    print('Fitting Spline!')
    wfit = SmoothBivariateSpline(xcent_big,ycent_big,wcent_big,kx=xfitd,ky=3,
                                 bbox=[0,img.shape[1],0,img.shape[0]] )

    return wfit

##########################
def mapwavelength(trace, wavemap):
    # use the wavemap from the HeNeAr_fit routine to determine the wavelength along the trace
    trace_wave = wavemap.ev(np.arange(len(trace)), trace)
    return trace_wave


#########################
def biascombine(biaslist, output='BIAS.fits', trim=True):
    # assume biaslist is a simple text file with image names
    # e.g. ls flat.00*b.fits > bflat.lis
    files = np.loadtxt(biaslist,dtype='string')

    for i in range(0,len(files)):
        hdu_i = fits.open(files[i])

        if trim is False:
            im_i = hdu_i[0].data
        if trim is True:
            datasec = hdu_i[0].header['DATASEC'][1:-1].replace(':',',').split(',')
            d = map(float, datasec)
            im_i = hdu_i[0].data[d[2]-1:d[3],d[0]-1:d[1]]

        # create image stack
        if (i==0):
            all_data = im_i
        elif (i>0):
            all_data = np.dstack( (all_data, im_i) )
        hdu_i.close(closed=True)

    # do median across whole stack
    bias = np.median(all_data, axis=2)

    # write output to disk for later use
    hduOut = fits.PrimaryHDU(bias)
    hduOut.writeto(output, clobber=True)
    return bias

#########################
def flatcombine(flatlist, bias, output='FLAT.fits', trim=True):
    # read the bias in, BUT we don't know if it's the numpy array or file name
    if isinstance(bias, str):
        # read in file if a string
        bias_im = fits.open(bias)[0].data
    else:
        # assume is proper array from biascombine function
        bias_im = bias

    # assume flatlist is a simple text file with image names
    # e.g. ls flat.00*b.fits > bflat.lis
    files = np.loadtxt(flatlist,dtype='string')

    for i in range(0,len(files)):
        hdu_i = fits.open(files[i])
        if trim is False:
            im_i = hdu_i[0].data - bias_im
        if trim is True:
            datasec = hdu_i[0].header['DATASEC'][1:-1].replace(':',',').split(',')
            d = map(float, datasec)
            im_i = hdu_i[0].data[d[2]-1:d[3],d[0]-1:d[1]] - bias_im

        # check for bad regions (not illuminated) in the spatial direction
        ycomp = im_i.sum(axis=1) # compress to y-axis only
        illum_thresh = 0.8 # value compressed data must reach to be used for flat normalization
        ok = np.where( (ycomp>= np.median(ycomp)*illum_thresh) )

        # assume a median scaling for each flat to account for possible different exposure times
        if (i==0):
            all_data = im_i / np.median(im_i[ok,:])
        elif (i>0):
            all_data = np.dstack( (all_data, im_i / np.median(im_i[ok,:])) )
        hdu_i.close(closed=True)

    # do median across whole stack
    flat = np.median(all_data, axis=2)
    # need other scaling?

    # write output to disk for later use
    hduOut = fits.PrimaryHDU(flat)
    hduOut.writeto(output, clobber=True)
    return flat ,ok[0]
# i want to return the flat "OK" mask to use later,
# but only optionally.... need to make a flat class!


#########################
def autoreduce(speclist, flatlist, biaslist, HeNeAr_file,
               trim=True, write_reduced=True, display=True):

    # assume specfile is a list of file names of object
    bias = biascombine(biaslist, trim = True)
    flat,fmask_out = flatcombine(flatlist, bias, trim=True)

    specfile = np.loadtxt(speclist,dtype='string')

    for spec in specfile:
        hdu = fits.open(spec)
        if trim is False:
            raw = hdu[0].data
        if trim is True:
            datasec = hdu[0].header['DATASEC'][1:-1].replace(':',',').split(',')
            d = map(float, datasec)
            raw = hdu[0].data[d[2]-1:d[3],d[0]-1:d[1]]
        hdu.close(closed=True)

        # remove bias and flat
        data = (raw - bias) / flat

        # with reduced data, trace the aperture
        trace = ap_trace(data, nsteps=50)

        # extract the spectrum
        ext_spec = ap_extract(data, trace, apwidth=3)

        # measure sky values along trace
        sky = sky_fit(data, trace, apwidth=3)

        xbins = np.arange(data.shape[1])
        if display is True:
            plt.figure()
            plt.imshow(np.log10(data), origin = 'lower',aspect='auto')
            plt.colorbar()
            plt.plot(xbins, trace,'k',lw=1)
            plt.plot(xbins, trace-3.,'r',lw=1)
            plt.plot(xbins, trace+3.,'r',lw=1)
            plt.show()

        # write output file for extracted spectrum
        if write_reduced is True:
            np.savetxt(spec+'.apextract',ext_spec-sky)

        wfit = HeNeAr_fit(HeNeAr_file,trim=True,fmask=fmask_out,display=False)

        wfinal = mapwavelength(trace,wfit)

        plt.figure()
        plt.plot(wfinal, ext_spec-sky)
        plt.xlabel('Wavelength')
        plt.ylabel('Counts')
        plt.title(spec)
        plt.show()

    return



#########################
#  Test Data / Examples
#########################

# autoreduce('robj.lis','rflat.lis', 'rbias.lis','example_data/HeNeAr.0027r.fits',
#            trim=True, display=False)

# autoreduce('bobj.lis','bflat.lis', 'bbias.lis','example_data/HeNeAr.0028b.fits',
#            trim=True, display=False)