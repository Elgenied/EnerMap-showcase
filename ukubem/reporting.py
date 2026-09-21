"""Publication outputs from the single current, uncalibrated results tree."""
from pathlib import Path
import json
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import geopandas as gpd
from matplotlib.colors import TwoSlopeNorm
from .unified import ROOT, OUT, BASE, load_inputs

FIG = OUT/'figures'


def style():
    FIG.mkdir(parents=True,exist_ok=True)
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.spines.top':False,
        'axes.spines.right':False,'axes.titleweight':'bold','figure.dpi':120})


def save(fig,name):
    for ext in ['png','pdf','svg']:fig.savefig(FIG/f'{name}.{ext}',dpi=260,bbox_inches='tight',facecolor='white')
    plt.close(fig)


def annotate_map(ax,g):
    xmin,ymin,xmax,ymax=g.total_bounds
    sx=xmin+.05*(xmax-xmin);sy=ymin+.015*(ymax-ymin)
    ax.plot([sx,sx+5000],[sy,sy],color='#44545b',lw=2)
    ax.text(sx+2500,sy+.025*(ymax-ymin),'5 km',ha='center',fontsize=8)
    ax.annotate('N',xy=(.94,.93),xytext=(.94,.8),xycoords='axes fraction',ha='center',arrowprops={'arrowstyle':'-|>','color':'#44545b'})
    ax.set_axis_off()


def validation():
    style(); inp,_=load_inputs()
    val=pd.read_csv(BASE/'lsoa_validation.csv').set_index('LSOA')
    metrics=json.loads((BASE/'metrics.json').read_text())
    for fuel in ['gas','elec']:val[f'{fuel}_error_pct']=100*(val[f'sim_{fuel}_mean_kWh']/val[f'{fuel}_mean_kWh']-1)
    colors=['#267c88','#9a649f']
    fig,axs=plt.subplots(1,2,figsize=(11,4.7))
    for ax,fuel,title,col in zip(axs,['gas','elec'],['(a) Gas','(b) Electricity'],colors):
        x=val[f'{fuel}_mean_kWh']/1000;y=val[f'sim_{fuel}_mean_kWh']/1000;lim=max(x.max(),y.max())*1.08
        ax.fill_between([0,lim],[0,.9*lim],[0,1.1*lim],color='#eee',label='±10% band')
        ax.plot([0,lim],[0,lim],'--',color='#58656b',label='Equal modelled and observed')
        ax.scatter(x,y,color=col,s=29,alpha=.85,edgecolor='white',linewidth=.3)
        ax.set(xlim=(0,lim),ylim=(0,lim),xlabel='Observed (MWh / consuming meter / year)',ylabel='Modelled (MWh / eligible dwelling / year)',title=title)
        ax.text(.04,.96,f"WAPE {metrics[fuel+'_WAPE_pct']:.2f}%\nBias {metrics[fuel+'_NMBE_pct']:+.2f}%\nPredictive R² {metrics[fuel+'_R2_predictive']:.3f}\nn = {len(val)} LSOAs",transform=ax.transAxes,va='top')
        ax.set_aspect('equal',adjustable='box')
    axs[1].legend(loc='lower right',fontsize=8);fig.tight_layout();save(fig,'06_validation_observed_modelled')
    geo=gpd.read_file(ROOT/'data/inputs/spatial/lsoa/lsoa.shp')[['LSOA','geometry']]
    g=geo.merge(val.reset_index(),on='LSOA',validate='one_to_one');assert len(g)==len(val)==84
    g.to_file(BASE/'lsoa_validation.gpkg',layer='validation',driver='GPKG',index=False)
    bound=max(val[['gas_error_pct','elec_error_pct']].abs().max());norm=TwoSlopeNorm(vmin=-bound,vcenter=0,vmax=bound)
    fig,axs=plt.subplots(1,2,figsize=(11,5.3))
    for ax,fuel,title in zip(axs,['gas','elec'],['(a) Gas','(b) Electricity']):
        g.plot(column=f'{fuel}_error_pct',ax=ax,cmap='RdBu_r',norm=norm,edgecolor='white',linewidth=.35)
        annotate_map(ax,g);ax.set_title(title)
    fig.subplots_adjust(left=.02,right=.87,bottom=.04,top=.9,wspace=.03)
    fig.colorbar(plt.cm.ScalarMappable(norm=norm,cmap='RdBu_r'),cax=fig.add_axes([.90,.2,.018,.57])).set_label('Modelled − observed (%)')
    save(fig,'06_validation_maps')
    values=[val.gas_error_pct.to_numpy(),val.elec_error_pct.to_numpy()]
    fig,axs=plt.subplots(1,2,figsize=(10,4.5))
    bp=axs[0].boxplot(values,tick_labels=['Gas','Electricity'],patch_artist=True,showfliers=False)
    rng=np.random.default_rng(42)
    for i,(v,col) in enumerate(zip(values,colors)):
        bp['boxes'][i].set_facecolor(col);bp['boxes'][i].set_alpha(.25)
        axs[0].scatter(np.full(len(v),i+1)+rng.uniform(-.12,.12,len(v)),v,color=col,s=12,alpha=.65)
        axs[0].text(i+1,v.max()+3,f'Median {np.median(v):+.1f}%',ha='center',fontsize=9)
        axs[1].step(np.sort(abs(v)),100*np.arange(1,len(v)+1)/len(v),where='post',color=col,label=['Gas','Electricity'][i])
    axs[0].axhline(0,color='grey',ls='--');axs[0].set(ylabel='Modelled − observed (%)',title='(a) Signed LSOA differences',ylim=(min(min(v) for v in values)-5,max(max(v) for v in values)+10))
    axs[1].axvline(10,color='grey',ls='--');axs[1].set(xlabel='Absolute area-level difference (%)',ylabel='LSOAs at or below this difference (%)',title='(b) Coverage of agreement',ylim=(0,103));axs[1].legend()
    fig.tight_layout();save(fig,'06_canet_style_distribution')
    val['joint_absolute_error_pct']=val[['gas_error_pct','elec_error_pct']].abs().mean(axis=1)
    rank=val.sort_values('joint_absolute_error_pct');rank.to_csv(BASE/'lsoa_alignment_ranking.csv')
    fig,axs=plt.subplots(1,2,figsize=(11,5.8))
    for ax,frame,title in [(axs[0],rank.head(8),'(a) Closest combined agreement'),(axs[1],rank.tail(8).iloc[::-1],'(b) Largest combined discrepancies')]:
        for fuel,offset,col,label in [('gas',-.12,colors[0],'Gas'),('elec',.12,colors[1],'Electricity')]:ax.scatter(frame[fuel+'_error_pct'],np.arange(len(frame))+offset,color=col,s=40,label=label)
        ax.axvspan(-10,10,color='#eee',zorder=-1);ax.axvline(0,color='grey',lw=.8)
        ax.set(yticks=np.arange(len(frame)),yticklabels=frame.lsoa_name,xlabel='Modelled − observed (%)',title=title,xlim=(-35,35));ax.invert_yaxis();ax.legend(fontsize=8)
    fig.tight_layout();save(fig,'06_lsoa_alignment')
    def describe(f):return '; '.join(f'{r.lsoa_name} ({i}: gas {r.gas_error_pct:+.1f}%, electricity {r.elec_error_pct:+.1f}%)' for i,r in f.iterrows())
    text=f'''# Uncalibrated validation and interpretation

Gas WAPE is {metrics['gas_WAPE_pct']:.2f}% and electricity WAPE is {metrics['elec_WAPE_pct']:.2f}%. Signed aggregate bias is {metrics['gas_NMBE_pct']:+.2f}% and {metrics['elec_NMBE_pct']:+.2f}%, respectively. Gas is within ±10% in {sum(abs(val.gas_error_pct)<=10)} of 84 LSOAs; electricity in {sum(abs(val.elec_error_pct)<=10)}. This band is descriptive, not a formal acceptance criterion.

The closest combined matches are {describe(rank.head(3))}. The largest combined discrepancies are {describe(rank.tail(3).iloc[::-1])}. No areas have been removed. The ranking gives equal weight to each area's absolute gas and electricity percentage errors; it is not WAPE.

The map shows generally modest gas underprediction, while electricity discrepancies are more spatially varied. Residuals locate disagreement but do not establish its cause. Possible factors requiring separate checks include dwelling/meter population differences, heating-system classification, building attributes, annual usage assumptions and the TMY-versus-meter weather basis. Good totals can conceal opposing local errors.

The Canet-inspired box plot shows signed delivered-gas/electricity discrepancies, 100 × (modelled − observed)/observed. It is not a replication of Canet's gas-to-heat comparison and does not use his sign/reference convention. All 84 points are shown. Boxes represent the middle 50% and median, not confidence intervals. Whiskers extend to observations within 1.5 IQR. The median is not WAPE.

The thermal solver supplies hourly useful space heat. Annual appliances, lighting, electric cooking and hot water retain the BREDEM/SAP-based estimates. Normalized profiles distribute those quantities across the year; raw generator sums are not a second annual energy account. Hot water at the tap, system-side hot-water heat including losses, and electric-shower electricity are distinct fields. Boiler efficiencies convert heat to fuel. Existing heat pumps use hourly COP with radiator sink 40 − outdoor temperature and DHW sink 50°C. Annual HP electricity is the sum of hourly electricity, with no fixed-SPF rescaling. The aggregate heat-pump family is treated as ASHP; defrost, backup and part-load effects are not resolved.

No calibration or spatial refit is performed. These observations have already informed model inspection, so the comparison is not an untouched holdout. Annual area-level agreement does not validate individual dwellings, hourly peaks, household behaviour, COP performance or scenarios. The existing weather and population conventions remain limitations.
'''
    (BASE/'DISCUSSION.md').write_text(text,encoding='utf-8')


def atlas():
    style();inp,_=load_inputs();st=pd.read_parquet(BASE/'stock_demand.parquet')
    water=__import__('ukubem.accounting',fromlist=['usage_for'])
    u=water.usage_for(inp.floor_area_final.to_numpy(float),water.family_masks(inp.systems_fam,inp.gas_connected.fillna(False)))
    st['dhw_useful_kWh']=u['dhw_useful_kWh'];st['total_delivered_kWh']=st.delivered_gas_kWh+st.delivered_elec_kWh+st.delivered_other_kWh
    geo=gpd.read_file(ROOT/'data/inputs/spatial/lsoa/lsoa.shp')[['LSOA','geometry']]
    cols=['Q_H_kWh','dhw_useful_kWh','delivered_gas_kWh','delivered_elec_kWh','delivered_other_kWh','total_delivered_kWh','floor_area_final']
    sums=st[cols].groupby(inp.LSOA).sum();sums['dwellings']=inp.groupby('LSOA').size()
    sums.to_csv(BASE/'lsoa_demand_totals.csv')
    g=geo.merge(sums.reset_index(),on='LSOA',validate='one_to_one')
    g.to_file(BASE/'lsoa_demand.gpkg',layer='demand',driver='GPKG',index=False)
    fig,axs=plt.subplots(2,2,figsize=(11,10))
    for ax,col,title,cmap in zip(axs.flat,['Q_H_kWh','dhw_useful_kWh','delivered_elec_kWh','total_delivered_kWh'],
        ['(a) Useful space heat','(b) Useful hot water at taps','(c) Delivered electricity','(d) Total delivered energy'],['YlOrRd','YlOrBr','PuBu','YlGnBu']):
        g.assign(value=g[col]/1e6).plot(column='value',ax=ax,cmap=cmap,edgecolor='white',linewidth=.3,legend=True,legend_kwds={'label':'GWh/year per LSOA','shrink':.65,'pad':.01})
        annotate_map(ax,g);ax.set_title(title)
    fig.tight_layout();save(fig,'09_demand_totals_maps')
    buildings=gpd.read_file(ROOT/'data/inputs/spatial/buildings.gpkg').to_crs(27700)
    by=st[cols].groupby(inp.toid).sum()
    by['heat_intensity']=by.Q_H_kWh/by.floor_area_final
    bg=buildings[['toid','geometry']].merge(by.reset_index(),on='toid',how='left',validate='many_to_one')
    bg.to_parquet(BASE/'building_demand_georeferenced.parquet')
    fig,axs=plt.subplots(1,2,figsize=(13,6))
    for ax,col,title,label,cmap in [(axs[0],'heat_intensity','(a) Useful space-heat intensity','kWh / m² / year','YlOrRd'),
        (axs[1],'total_delivered_kWh','(b) Total delivered energy per building','MWh / building / year','viridis')]:
        value=bg[col] if col=='heat_intensity' else bg[col]/1000
        bound=float(value.quantile(.98))
        bg.plot(ax=ax,color='#e0e3e4',linewidth=0,rasterized=True)
        bg.assign(value=value).dropna(subset=['value']).plot(column='value',ax=ax,cmap=cmap,vmin=0,vmax=bound,linewidth=0,rasterized=True,legend=True,legend_kwds={'label':label+' (colour capped at P98)','shrink':.55,'extend':'max'})
        geo.boundary.plot(ax=ax,color='#a9b1b5',linewidth=.2)
        xmin,ymin,xmax,ymax=geo.total_bounds;ax.set_xlim(xmin,xmax);ax.set_ylim(ymin,ymax)
        annotate_map(ax,geo);ax.set_title(title)
    fig.tight_layout();save(fig,'09_building_demand_maps')
    # A dense, objectively selected neighbourhood makes individual footprints legible.
    from scipy.ndimage import uniform_filter
    cent=bg.loc[bg.Q_H_kWh.notna()].geometry.centroid
    xb=np.arange(cent.x.min(),cent.x.max()+500,500);yb=np.arange(cent.y.min(),cent.y.max()+500,500)
    density,_,_=np.histogram2d(cent.x,cent.y,bins=[xb,yb])
    i,j=np.unravel_index(np.argmax(uniform_filter(density,size=3,mode='constant')),density.shape)
    cx=(xb[i]+xb[i+1])/2;cy=(yb[j]+yb[j+1])/2;half=750
    detail=bg.cx[cx-half:cx+half,cy-half:cy+half]
    fig,axs=plt.subplots(1,3,figsize=(14,5))
    for ax,col,title,cmap in zip(axs,['Q_H_kWh','dhw_useful_kWh','delivered_elec_kWh'],
        ['(a) Useful space heat','(b) Useful hot water','(c) Delivered electricity'],['YlOrRd','YlOrBr','PuBu']):
        value=detail[col]/1000;cap=float((bg[col]/1000).quantile(.98))
        detail.plot(ax=ax,color='#ddd',edgecolor='white',linewidth=.15)
        detail.assign(value=value).dropna(subset=['value']).plot(column='value',ax=ax,cmap=cmap,vmin=0,vmax=cap,edgecolor='#777',linewidth=.10,
            legend=True,legend_kwds={'label':'MWh / building / year','shrink':.52,'extend':'max','pad':.01})
        ax.set_xlim(cx-half,cx+half);ax.set_ylim(cy-half,cy+half);ax.set_axis_off();ax.set_title(title)
        ax.plot([cx-half+60,cx-half+260],[cy-half+70,cy-half+70],color='#333',lw=2)
        ax.text(cx-half+160,cy-half+100,'200 m',ha='center',fontsize=8)
    fig.tight_layout();save(fig,'09_neighbourhood_demand_detail')
    (BASE/'map_detail_extent.json').write_text(json.dumps({'selection':'densest 3x3 grid of 500m residential-footprint cells',
        'crs':'EPSG:27700','bounds':[cx-half,cy-half,cx+half,cy+half],'footprints_with_demand':int(detail.Q_H_kWh.notna().sum())},indent=2),encoding='utf-8')
    hr=pd.read_csv(BASE/'stock_hourly.csv',parse_dates=['time']).set_index('time')
    monthly=hr[['space_heat_kWh','dhw_useful_kWh','delivered_electricity_kWh']].resample('MS').sum()/1e6
    fig,axs=plt.subplots(1,2,figsize=(11,4.2))
    monthly.set_axis(monthly.index.month).rename(columns={'space_heat_kWh':'Space heat','dhw_useful_kWh':'Useful DHW','delivered_electricity_kWh':'Delivered electricity'}).plot.bar(ax=axs[0],color=['#c07843','#c6ad68','#267c88'],rot=0)
    axs[0].set(xlabel='Month',ylabel='GWh',title='(a) Monthly demand');axs[0].legend(fontsize=8)
    axs[1].plot(np.sort(hr.delivered_electricity_kWh/1000)[::-1],color='#267c88',label='All modelled electricity')
    axs[1].plot(np.sort(hr.hp_electricity_kWh/1000)[::-1],color='#9a649f',label='Existing heat pumps')
    axs[1].set(xlabel='Hours sorted by demand',ylabel='MW',title='(b) Modelled load-duration curves');axs[1].legend(fontsize=8)
    fig.tight_layout();save(fig,'09_annual_and_hourly_demand')
    (BASE/'MAP_NOTES.md').write_text('All maps cover the borough stock, without the ten-ward boundary. LSOA totals are sums, not average dwelling values. Building heat intensity is total useful space heat divided by total dwelling floor area in the footprint. Building delivered energy is the sum across dwellings. Only colour scales on building maps are capped at the 98th percentile; all values remain in exports. Grey footprints have no mapped residential demand. Useful heat and delivered electricity overlap in end-use boundaries and must not be added together. Total delivered energy sums gas, electricity and other fuels once. Hourly peaks are modelled, not validated against hourly meters.',encoding='utf-8')


def scenario_figures():
    style();summary=pd.read_csv(OUT/'scenarios/scenario_summary.csv');x=summary[summary.scope=='borough'].set_index('scenario')
    fig,axs=plt.subplots(1,2,figsize=(12,4.8))
    x[['delivered_gas_GWh','delivered_elec_GWh','delivered_other_GWh']].rename(columns={'delivered_gas_GWh':'Gas','delivered_elec_GWh':'Electricity','delivered_other_GWh':'Other fuels'}).plot.bar(ax=axs[0],stacked=True,color=['#c07843','#267c88','#aaa'])
    axs[0].set(ylabel='GWh/year',xlabel='',title='(a) Delivered energy before PV offsets')
    x.carbon_kt.plot.bar(ax=axs[1],color='#528d72');axs[1].set(ylabel='ktCO₂e/year',xlabel='',title='(b) Carbon: retained project factors')
    for ax in axs:ax.tick_params(axis='x',rotation=35,labelsize=8)
    fig.tight_layout();save(fig,'10_scenario_energy_carbon')
    geo=gpd.read_file(ROOT/'data/inputs/spatial/lsoa/lsoa.shp')[['LSOA','geometry']]
    base=pd.read_parquet(OUT/'scenarios/U0_baseline.parquet');retro=pd.read_parquet(OUT/'scenarios/U2_envelope.parquet')
    delta=(base.Q_H_kWh-retro.Q_H_kWh).groupby(base.LSOA).sum()/1e6
    g=geo.merge(delta.rename('saving').reset_index(),on='LSOA')
    fig,ax=plt.subplots(figsize=(7,6));g.plot(column='saving',ax=ax,cmap='YlGnBu',legend=True,legend_kwds={'label':'Useful heat saved (GWh/year)','shrink':.6},edgecolor='white',linewidth=.4)
    annotate_map(ax,g);ax.set_title('Deep fabric package: modelled space-heat saving');save(fig,'10_retrofit_saving_map')


def interface_figures():
    style();p=OUT/'pypsa_interface';tot=pd.read_csv(p/'cluster_annual_totals.csv')
    clusters=pd.read_csv(p/'cluster_summary.csv')
    sums=tot.groupby('package_id')[['space_heat_kWh','hot_water_kWh','non_heating_elec_kWh']].sum()/1e6
    fig,axs=plt.subplots(1,2,figsize=(11,4.2))
    sums.rename(columns={'space_heat_kWh':'Space heat','hot_water_kWh':'Useful DHW','non_heating_elec_kWh':'Non-heating electricity'}).plot.bar(ax=axs[0],color=['#c07843','#c6ad68','#267c88'],rot=0)
    axs[0].set(xlabel='Alternative retrofit package',ylabel='GWh/year',title='(a) PyPSA demand inputs');axs[0].legend(fontsize=8)
    axs[1].hist(clusters.dwelling_count,bins=np.logspace(0,np.log10(clusters.dwelling_count.max()+1),30),color='#267c88')
    axs[1].set(xscale='log',xlabel='Dwellings per cluster',ylabel='Clusters',title='(b) Interface aggregation')
    fig.tight_layout();save(fig,'11_pypsa_interface_summary')
